"""Analysis orchestration service.

Contains the logic for running individual analyzers, running all three
analyzers, and the full heapdump upload-to-analysis pipeline.
"""

import asyncio
import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException, UploadFile, status

from analyzers import (
    MATLeakSuspectsAnalyzer,
    MATSystemOverviewAnalyzer,
    MATTopComponentsAnalyzer,
)
from config import get_settings
from models import AnalyzeRequest
from services.mat_runner import find_report, run_mat

logger = logging.getLogger("mat-service")


def resolve_output(output_dir: Optional[str], prefix: str) -> str:
    """Resolve or create an output directory for analyzer results."""
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return output_dir
    return tempfile.mkdtemp(prefix=f"mat_{prefix}_")


def run_analyzer(analyzer_cls, request: AnalyzeRequest) -> Dict[str, Any]:
    """Run a single analyzer class and return structured result dict."""
    report_path = Path(request.report_path)
    if not report_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report not found: {request.report_path}",
        )
    out_dir = resolve_output(request.output_dir, analyzer_cls.__name__)
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


def run_all_analyzers(
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
        report_zip = find_report(reports_dir, patterns)
        if report_zip is None:
            result[key] = {"status": "skipped", "reason": "No matching ZIP found"}
            continue
        out = resolve_output(output_dir, key)
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


async def heapdump_pipeline(
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
    settings = get_settings()
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
    total_bytes = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=(
                            f"Upload exceeds maximum size of "
                            f"{settings.max_upload_size_bytes / (1024**3):.0f} GB"
                        ),
                    )
                fh.write(chunk)
    except HTTPException:
        # Clean up partial file on size limit
        dest.unlink(missing_ok=True)
        raise
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
    mat_result = await loop.run_in_executor(None, run_mat, dest, rpt_dir)

    if mat_result["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"MAT execution failed: {mat_result['error']}",
        )

    # ── Run Python analysers (CPU-bound → thread pool) ────────────────────────
    analyzer_tmp = tempfile.mkdtemp(prefix="mat_analysis_")
    logger.info("Running Python analysers on %s…", rpt_dir)
    analysis = await loop.run_in_executor(
        None, run_all_analyzers, rpt_dir, analyzer_tmp, True   # always generate text
    )

    # ── Delete temporary ZIPs (keep /reports clean) ───────────────────────────
    for zip_path in mat_result.get("reports_generated", []):
        try:
            Path(zip_path).unlink(missing_ok=True)
            logger.info("Deleted temporary MAT report: %s", zip_path)
        except Exception as exc:
            logger.warning("Could not delete %s: %s", zip_path, exc)

    # ── Delete uploaded .hprof — analysis is complete, no longer needed ──────
    try:
        dest.unlink(missing_ok=True)
        logger.info("Deleted uploaded heap dump: %s", dest)
    except Exception as exc:
        logger.warning("Could not delete %s: %s", dest, exc)

    # ── Delete analyzer temp directory (extracted HTML, etc.) ────────────────
    try:
        shutil.rmtree(analyzer_tmp, ignore_errors=True)
        logger.info("Deleted analyzer temp dir: %s", analyzer_tmp)
    except Exception as exc:
        logger.warning("Could not delete %s: %s", analyzer_tmp, exc)

    return filename, size_mb, dest, mat_result, analysis
