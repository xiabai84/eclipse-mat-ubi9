"""Analysis endpoints: heap dump upload and report analysis."""

import asyncio
import functools
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse, PlainTextResponse

from analyzers import (
    MATLeakSuspectsAnalyzer,
    MATSystemOverviewAnalyzer,
    MATTopComponentsAnalyzer,
)
from config import get_settings
from models import AllAnalyzeRequest, AnalyzeRequest
from services.analysis_service import (
    heapdump_pipeline,
    run_all_analyzers,
    run_analyzer,
)

router = APIRouter(tags=["Analysis"])


@router.post("/analyze/heapdump")
async def analyze_heapdump(
    file: UploadFile = File(..., description="Java heap dump file (.hprof)"),
    heapdumps_dir: str = Form(None),
    reports_dir: str = Form(None),
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
    settings = get_settings()
    if heapdumps_dir is None:
        heapdumps_dir = settings.heapdumps_dir
    if reports_dir is None:
        reports_dir = settings.reports_dir

    filename, size_mb, dest, mat_result, analysis = await heapdump_pipeline(
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


@router.post(
    "/analyze/heapdump/report",
    response_class=PlainTextResponse,
)
async def analyze_heapdump_report(
    file: UploadFile = File(..., description="Java heap dump file (.hprof)"),
    heapdumps_dir: str = Form(None),
    reports_dir: str = Form(None),
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
    - Issue list with severity icons
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
    settings = get_settings()
    if heapdumps_dir is None:
        heapdumps_dir = settings.heapdumps_dir
    if reports_dir is None:
        reports_dir = settings.reports_dir

    _, _, _, _, analysis = await heapdump_pipeline(
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


@router.post("/analyze/suspects")
async def analyze_suspects(request: AnalyzeRequest) -> JSONResponse:
    """
    Analyse a pre-generated **Leak Suspects** ZIP report.

    Returns structured analysis data and (optionally) a human-readable report
    with Java-specific root-cause analysis and fix recommendations.
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_analyzer, MATLeakSuspectsAnalyzer, request)
    return JSONResponse(result)


@router.post("/analyze/overview")
async def analyze_overview(request: AnalyzeRequest) -> JSONResponse:
    """Analyse a pre-generated **System Overview** ZIP report."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_analyzer, MATSystemOverviewAnalyzer, request)
    return JSONResponse(result)


@router.post("/analyze/top-components")
async def analyze_top_components(request: AnalyzeRequest) -> JSONResponse:
    """Analyse a pre-generated **Top Components** ZIP report."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, run_analyzer, MATTopComponentsAnalyzer, request)
    return JSONResponse(result)


@router.post("/analyze/all")
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
        None, functools.partial(run_all_analyzers, reports_path, request.output_dir, request.include_text)
    )
    return JSONResponse({"status": "ok", "reports_dir": request.reports_dir, **analysis})
