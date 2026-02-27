#!/usr/bin/env python3
"""
MAT Analysis REST Service  v3.1
================================
A FastAPI service that exposes Eclipse Memory Analyzer Tool (MAT) report
analysis over HTTP — including direct Java heap dump upload and analysis.

Endpoints
---------
GET  /health                     — liveness probe
GET  /reports                    — list ZIP reports in /reports
POST /analyze/heapdump           — upload a .hprof file → run MAT → JSON response
POST /analyze/heapdump/report    — upload a .hprof file → run MAT → plain-text report
POST /analyze/suspects           — analyse an existing Leak Suspects ZIP
POST /analyze/overview           — analyse an existing System Overview ZIP
POST /analyze/top-components     — analyse an existing Top Components ZIP
POST /analyze/all                — auto-discover and run all three analyses
GET  /docs                       — Swagger UI

Usage inside Docker (service mode)
-----------------------------------
  docker run -p 8080:8080 \\
    -v $(pwd)/heapdumps:/heapdumps \\
    -v $(pwd)/reports:/reports   \\
    eclipse-mat service

Quick tests:
  curl http://localhost:8080/health

  # Full pipeline — JSON response:
  curl -X POST http://localhost:8080/analyze/heapdump \\
       -F "file=@./heapdumps/demo.hprof"

  # Full pipeline — human-readable plain-text report (terminal-friendly):
  curl -X POST http://localhost:8080/analyze/heapdump/report \\
       -F "file=@./heapdumps/demo.hprof"

  # Analyse pre-generated ZIP reports:
  curl -X POST http://localhost:8080/analyze/all \\
       -H "Content-Type: application/json" \\
       -d '{"reports_dir": "/reports"}'
"""

import asyncio
import functools
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent))

from analyzers import (
    MATLeakSuspectsAnalyzer,
    MATSystemOverviewAnalyzer,
    MATTopComponentsAnalyzer,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("mat-service")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MAT Analysis Service",
    description=(
        "REST API for running Eclipse Memory Analyzer Tool (MAT) reports and "
        "analysing the results with Java-specific recommendations.\n\n"
        "**Heap-dump upload workflows:**\n\n"
        "- `POST /analyze/heapdump` — structured **JSON** response (machine-readable).\n"
        "- `POST /analyze/heapdump/report` — **plain-text** report for human reading "
        "  in a terminal (`curl … | cat`). Same pipeline, different output format.\n\n"
        "**Pre-generated report ZIPs** (`POST /analyze/suspects` etc.) — analyse "
        "existing MAT report ZIPs that you have already generated."
    ),
    version="3.1.0",
)

DEFAULT_REPORTS_DIR  = "/reports"
DEFAULT_HEAPDUMPS_DIR = "/heapdumps"
MAT_SCRIPT           = "/opt/eclipse-mat/ParseHeapDump.sh"
MAT_TIMEOUT_SECONDS  = int(os.environ.get("MAT_TIMEOUT", "600"))   # 10 min default


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    report_path: str = Field(
        ...,
        description="Absolute path to the MAT report ZIP file inside the container",
        example="/reports/MyApp_Leak_Suspects.zip",
    )
    output_dir: Optional[str] = Field(
        None,
        description="Directory for extracted files and output (defaults to temp dir)",
    )
    include_text: bool = Field(
        True, description="Include the human-readable text report in the response"
    )


class AllAnalyzeRequest(BaseModel):
    reports_dir: str = Field(
        DEFAULT_REPORTS_DIR,
        description="Directory containing the MAT report ZIP files",
    )
    output_dir: Optional[str] = Field(None, description="Override output directory")
    include_text: bool = Field(True, description="Include text reports in the response")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_output(output_dir: Optional[str], prefix: str) -> str:
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return output_dir
    return tempfile.mkdtemp(prefix=f"mat_{prefix}_")


def _run_analyzer(analyzer_cls, request: AnalyzeRequest) -> Dict[str, Any]:
    """Run a single analyzer class and return structured result dict."""
    report_path = Path(request.report_path)
    if not report_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report not found: {request.report_path}",
        )
    out_dir = _resolve_output(request.output_dir, analyzer_cls.__name__)
    try:
        analyzer = analyzer_cls(str(report_path), out_dir)
        analyzer.analyze()
        result: Dict[str, Any] = {
            "status": "ok",
            "report_path": str(report_path),
            "output_dir": out_dir,
            "analysis": analyzer.report_data,
            "problems_found": len(analyzer.report_data.get("problems", [])),
        }
        if request.include_text:
            result["report_text"] = analyzer.generate_report()
        return result
    except Exception as exc:
        logger.exception("Analysis failed for %s", request.report_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {exc}",
        )


def _find_report(reports_dir: Path, suffix_patterns: List[str]) -> Optional[Path]:
    """Return the first ZIP whose name contains one of the suffix patterns."""
    for pattern in suffix_patterns:
        matches = list(reports_dir.glob(f"*{pattern}*.zip"))
        if matches:
            return matches[0]
    return None


def _run_mat(heapdump_path: Path, reports_dir: Path) -> Dict[str, Any]:
    """
    Execute Eclipse MAT ParseHeapDump.sh to generate all three report ZIPs.
    Returns a result dict with status, tail of stdout/stderr, and zip paths.
    """
    if not Path(MAT_SCRIPT).exists():
        return {
            "status": "error",
            "error": (
                f"Eclipse MAT not found at {MAT_SCRIPT}. "
                "Build the Docker image using the provided Dockerfile."
            ),
        }

    reports_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        MAT_SCRIPT,
        str(heapdump_path),
        "org.eclipse.mat.api:suspects",
        "org.eclipse.mat.api:overview",
        "org.eclipse.mat.api:top_components",
    ]
    logger.info("Running MAT: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=MAT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "error": (
                f"MAT timed out after {MAT_TIMEOUT_SECONDS}s. "
                "Set MAT_TIMEOUT env variable to a higher value for large heap dumps."
            ),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    # Move generated ZIPs from the dump directory to reports_dir
    dump_dir = heapdump_path.parent
    moved: List[str] = []
    for zip_file in dump_dir.glob("*.zip"):
        dest = reports_dir / zip_file.name
        shutil.move(str(zip_file), str(dest))
        moved.append(str(dest))
        logger.info("MAT report saved: %s", dest)

    # Clean up MAT index/cache files
    for suffix in ["index", "threads", "o2c", "inbound", "outbound", "array", "i2sv2"]:
        for f in dump_dir.glob(f"*.{suffix}*"):
            try:
                f.unlink()
            except OSError:
                pass

    return {
        "status": "ok" if proc.returncode == 0 else "warning",
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "reports_generated": moved,
    }


def _run_all_analyzers(
    reports_dir: Path,
    output_dir: Optional[str],
    include_text: bool,
) -> Dict[str, Any]:
    """Run all three analysers on *reports_dir* and merge results."""
    specs = [
        ("suspects",       MATLeakSuspectsAnalyzer,  ["Leak_Suspects", "Suspects", "leak"]),
        ("overview",       MATSystemOverviewAnalyzer, ["System_Overview", "Overview", "overview"]),
        ("top_components", MATTopComponentsAnalyzer,  ["Top_Components", "top_component"]),
    ]
    result: Dict[str, Any] = {
        "suspects": None, "overview": None, "top_components": None, "total_problems": 0,
    }
    for key, cls, patterns in specs:
        report_zip = _find_report(reports_dir, patterns)
        if report_zip is None:
            result[key] = {"status": "skipped", "reason": "No matching ZIP found"}
            continue
        out = _resolve_output(output_dir, key)
        try:
            analyzer = cls(str(report_zip), out)
            analyzer.analyze()
            entry: Dict[str, Any] = {
                "status": "ok",
                "report_path": str(report_zip),
                "problems_found": len(analyzer.report_data.get("problems", [])),
                "analysis": analyzer.report_data,
            }
            if include_text:
                entry["report_text"] = analyzer.generate_report()
            result[key] = entry
            result["total_problems"] += entry["problems_found"]
        except Exception as exc:
            logger.exception("Analysis failed for %s (%s)", report_zip, key)
            result[key] = {
                "status": "error",
                "report_path": str(report_zip),
                "error": str(exc),
            }
    return result


# ---------------------------------------------------------------------------
# Routes — Operations
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Operations"])
def health() -> Dict[str, Any]:
    """Liveness probe — returns HTTP 200 when the service is running."""
    return {
        "status": "ok",
        "service": "mat-analysis",
        "version": "3.1.0",
        "mat_available": Path(MAT_SCRIPT).exists(),
    }


@app.get("/reports", tags=["Operations"])
def list_reports(reports_dir: str = DEFAULT_REPORTS_DIR) -> Dict[str, Any]:
    """List MAT report ZIP files in *reports_dir*, grouped by report type."""
    path = Path(reports_dir)
    if not path.exists():
        return {"reports_dir": reports_dir, "reports": {}, "note": "Directory not found"}

    zips = sorted(path.glob("*.zip"))
    categorised: Dict[str, List[str]] = {
        "suspects": [], "overview": [], "top_components": [], "other": [],
    }
    for z in zips:
        name = z.name.lower()
        if "leak_suspect" in name or "suspects" in name:
            categorised["suspects"].append(str(z))
        elif "system_overview" in name or "overview" in name:
            categorised["overview"].append(str(z))
        elif "top_component" in name:
            categorised["top_components"].append(str(z))
        else:
            categorised["other"].append(str(z))

    return {"reports_dir": reports_dir, "total": len(zips), "reports": categorised}


# ---------------------------------------------------------------------------
# Shared upload + MAT pipeline
# ---------------------------------------------------------------------------

async def _heapdump_pipeline(
    file: UploadFile,
    heapdumps_dir: str,
    reports_dir: str,
) -> tuple:
    """
    Shared coroutine for the /analyze/heapdump* endpoints.

    Saves the uploaded .hprof, runs Eclipse MAT, runs all three Python
    analysers, cleans up generated ZIPs, and returns:

        (filename, size_mb, dest_path, mat_result, analysis_dict)

    Raises HTTPException on any fatal error.
    """
    filename = file.filename or "dump.hprof"
    if not filename.lower().endswith(".hprof"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .hprof heap dump files are accepted.",
        )

    # ── Save upload in 1 MB chunks ────────────────────────────────────────────
    hd_dir = Path(heapdumps_dir)
    hd_dir.mkdir(parents=True, exist_ok=True)
    unique_prefix = uuid.uuid4().hex[:12]
    dest = hd_dir / f"{unique_prefix}_{filename}"

    logger.info("Saving uploaded heap dump → %s", dest)
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save upload: {exc}",
        )
    finally:
        await file.close()

    size_mb = round(dest.stat().st_size / 1_048_576, 2)
    logger.info("Saved %s (%.1f MB)", dest, size_mb)

    rpt_dir = Path(reports_dir)

    # ── Run Eclipse MAT (CPU-bound → thread pool) ─────────────────────────────
    loop = asyncio.get_event_loop()
    logger.info("Running Eclipse MAT…")
    mat_result = await loop.run_in_executor(None, _run_mat, dest, rpt_dir)

    if mat_result["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MAT execution failed: {mat_result['error']}",
        )

    # ── Run Python analysers (CPU-bound → thread pool) ────────────────────────
    logger.info("Running Python analysers on %s…", rpt_dir)
    analysis = await loop.run_in_executor(
        None, _run_all_analyzers, rpt_dir, None, True   # always generate text
    )

    # ── Delete temporary ZIPs (keep /reports clean) ───────────────────────────
    for zip_path in mat_result.get("reports_generated", []):
        try:
            Path(zip_path).unlink(missing_ok=True)
            logger.info("Deleted temporary MAT report: %s", zip_path)
        except Exception as exc:
            logger.warning("Could not delete %s: %s", zip_path, exc)

    return filename, size_mb, dest, mat_result, analysis


# ---------------------------------------------------------------------------
# Routes — Analysis
# ---------------------------------------------------------------------------

_HEAPDUMP_FORM_PARAMS = dict(
    heapdumps_dir=Form(
        DEFAULT_HEAPDUMPS_DIR,
        description="Container directory where the upload is stored",
    ),
    reports_dir=Form(
        DEFAULT_REPORTS_DIR,
        description="Container directory where MAT writes the ZIP reports",
    ),
)


@app.post("/analyze/heapdump", tags=["Analysis"])
async def analyze_heapdump(
    file: UploadFile = File(..., description="Java heap dump file (.hprof)"),
    heapdumps_dir: str = Form(DEFAULT_HEAPDUMPS_DIR),
    reports_dir: str = Form(DEFAULT_REPORTS_DIR),
) -> JSONResponse:
    """
    **Full heap dump analysis pipeline — JSON response.**

    1. Upload a `.hprof` file via `multipart/form-data`.
    2. Eclipse MAT generates Leak Suspects, System Overview, and Top Components ZIPs.
    3. All three Python analysers run; structured JSON is returned.
    4. Generated ZIPs are deleted after the response is built.

    See `POST /analyze/heapdump/report` for the human-readable plain-text alternative.

    ```bash
    curl -X POST http://localhost:8080/analyze/heapdump \\
         -F "file=@./heapdumps/myapp.hprof"
    ```
    """
    filename, size_mb, dest, mat_result, analysis = await _heapdump_pipeline(
        file, heapdumps_dir, reports_dir
    )
    return JSONResponse({
        "status": "ok",
        "heapdump": {"filename": filename, "size_mb": size_mb, "saved_to": str(dest)},
        "mat": mat_result,
        "total_problems": analysis.get("total_problems", 0),
        "suspects":       analysis.get("suspects"),
        "overview":       analysis.get("overview"),
        "top_components": analysis.get("top_components"),
    })


@app.post(
    "/analyze/heapdump/report",
    tags=["Analysis"],
    response_class=PlainTextResponse,
)
async def analyze_heapdump_report(
    file: UploadFile = File(..., description="Java heap dump file (.hprof)"),
    heapdumps_dir: str = Form(DEFAULT_HEAPDUMPS_DIR),
    reports_dir: str = Form(DEFAULT_REPORTS_DIR),
    sections: str = Form(
        "suspects,overview,top_components",
        description=(
            "Comma-separated list of sections to include. "
            "Valid values: suspects, overview, top_components. "
            "Default: all three."
        ),
    ),
) -> PlainTextResponse:
    """
    **Full heap dump analysis pipeline — human-readable plain-text report.**

    Same pipeline as `POST /analyze/heapdump` but returns `text/plain` instead
    of JSON — ready to read directly in a terminal or pipe to a pager.

    The response body contains the visual analysis reports from the requested
    sections (Leak Suspects, System Overview, Top Components), separated by a
    blank line.  Each section includes:

    - Heap overview with progress bar
    - Issue list with severity icons (🔴 🟡 🔵)
    - Primary suspect block with class name, retained heap, class loader
    - Stack trace and key objects (if available)
    - Immediate action plan
    - Java Diagnostics Summary with root-cause analysis and fix steps

    ```bash
    # Print the full report directly in the terminal:
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\
         -F "file=@./heapdumps/myapp.hprof"

    # Pipe to a pager:
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\
         -F "file=@./heapdumps/myapp.hprof" | less

    # Save to a file:
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\
         -F "file=@./heapdumps/myapp.hprof" > analysis.txt

    # Request only the leak-suspects section:
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\
         -F "file=@./heapdumps/myapp.hprof" \\
         -F "sections=suspects"
    ```
    """
    _, _, _, _, analysis = await _heapdump_pipeline(
        file, heapdumps_dir, reports_dir
    )

    # Determine which sections to include
    requested = {s.strip().lower() for s in sections.split(",")}
    section_order = [
        ("suspects",       "suspects"),
        ("overview",       "overview"),
        ("top_components", "top_components"),
    ]

    parts: List[str] = []
    for key, label in section_order:
        if key not in requested and label not in requested:
            continue
        entry = analysis.get(key) or {}
        text = entry.get("report_text", "")
        if not text:
            status_msg = entry.get("status", "skipped")
            reason = entry.get("reason") or entry.get("error", "")
            parts.append(
                f"[{key.upper()}] — {status_msg}"
                + (f": {reason}" if reason else "")
            )
        else:
            parts.append(text)

    if not parts:
        return PlainTextResponse(
            "No analysis sections were generated. "
            "Check that Eclipse MAT is installed and the heap dump is valid.\n",
            status_code=500,
        )

    return PlainTextResponse("\n\n".join(parts) + "\n", media_type="text/plain; charset=utf-8")


@app.post("/analyze/suspects", tags=["Analysis"])
async def analyze_suspects(request: AnalyzeRequest) -> JSONResponse:
    """
    Analyse a pre-generated **Leak Suspects** ZIP report.

    Returns structured analysis data and (optionally) a human-readable report
    with Java-specific root-cause analysis and fix recommendations.
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_analyzer, MATLeakSuspectsAnalyzer, request)
    return JSONResponse(result)


@app.post("/analyze/overview", tags=["Analysis"])
async def analyze_overview(request: AnalyzeRequest) -> JSONResponse:
    """Analyse a pre-generated **System Overview** ZIP report."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_analyzer, MATSystemOverviewAnalyzer, request)
    return JSONResponse(result)


@app.post("/analyze/top-components", tags=["Analysis"])
async def analyze_top_components(request: AnalyzeRequest) -> JSONResponse:
    """Analyse a pre-generated **Top Components** ZIP report."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_analyzer, MATTopComponentsAnalyzer, request)
    return JSONResponse(result)


@app.post("/analyze/all", tags=["Analysis"])
async def analyze_all(request: AllAnalyzeRequest) -> JSONResponse:
    """
    Auto-discover and run all three analyses from a reports directory.

    The service scans *reports_dir* for ZIPs matching known MAT naming patterns.
    Partial results are returned even when some reports are missing.
    """
    reports_path = Path(request.reports_dir)
    if not reports_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reports directory not found: {request.reports_dir}",
        )
    loop = asyncio.get_event_loop()
    analysis = await loop.run_in_executor(
        None, functools.partial(_run_all_analyzers, reports_path, request.output_dir, request.include_text)
    )
    return JSONResponse({"status": "ok", "reports_dir": request.reports_dir, **analysis})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8080, log_level="info", reload=False)
