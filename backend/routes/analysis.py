"""Analysis endpoints: upload a heap dump, get the analysis as JSON or as plain text."""

from typing import List

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from auth import require_token
from services.analysis_service import heapdump_pipeline

router = APIRouter(tags=["Analysis"], dependencies=[Depends(require_token)])

DUMP = File(..., description="Java heap dump (.hprof or .hprof.gz)")


@router.post("/analyze/heapdump")
async def analyze_heapdump(file: UploadFile = DUMP) -> JSONResponse:
    """
    **Full heap dump analysis — JSON response.**

    1. Upload a `.hprof` or `.hprof.gz` file via `multipart/form-data`.
    2. Eclipse MAT generates the Leak Suspects, System Overview and Top Components reports.
    3. The three analysers run; structured JSON is returned.
    4. The dump and all intermediate files are deleted when the request ends.

    ```bash
    curl -X POST http://localhost:8080/analyze/heapdump \\
         -H "Authorization: Bearer $API_TOKEN" \\
         -F "file=@./myapp.hprof.gz"
    ```
    """
    filename, size_mb, mat_result, analysis = await heapdump_pipeline(file, include_text=True)
    return JSONResponse({
        "status": "ok",
        "heapdump": {"filename": filename, "size_mb": size_mb},
        "mat": {"status": mat_result["status"], "duration_s": mat_result.get("duration_s")},
        "total_problems": analysis.get("total_problems", 0),
        "suspects":       analysis.get("suspects"),
        "overview":       analysis.get("overview"),
        "top_components": analysis.get("top_components"),
    })


@router.post("/analyze/heapdump/report", response_class=PlainTextResponse)
async def analyze_heapdump_report(
    file: UploadFile = DUMP,
    sections: str = Form(
        "suspects,overview,top_components",
        description="Comma-separated sections: suspects, overview, top_components. Default: all three.",
    ),
) -> PlainTextResponse:
    """
    **Full heap dump analysis — human-readable plain-text report.**

    Same pipeline as `POST /analyze/heapdump`, returns `text/plain`.

    ```bash
    curl -s -X POST http://localhost:8080/analyze/heapdump/report \\
         -H "Authorization: Bearer $API_TOKEN" \\
         -F "file=@./myapp.hprof.gz" | less
    ```
    """
    _, _, _, analysis = await heapdump_pipeline(file, include_text=True)

    requested = {s.strip().lower() for s in sections.split(",")}
    parts: List[str] = []
    for key in ("suspects", "overview", "top_components"):
        if key not in requested:
            continue
        entry = analysis.get(key) or {}
        text = entry.get("report_text", "")
        if text:
            parts.append(text)
        else:
            reason = entry.get("reason") or entry.get("error", "")
            parts.append(f"[{key.upper()}] — {entry.get('status', 'skipped')}" + (f": {reason}" if reason else ""))

    if not parts:
        return PlainTextResponse("No analysis sections were requested or generated.\n", status_code=400)
    return PlainTextResponse("\n\n".join(parts) + "\n", media_type="text/plain; charset=utf-8")
