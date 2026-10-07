"""
MAT Analysis REST Service  v3.1
================================
A FastAPI service that exposes Eclipse Memory Analyzer Tool (MAT) report
analysis over HTTP — including direct Java heap dump upload and analysis.

Usage inside Docker (service mode)
-----------------------------------
  docker run -p 8080:8080 \\
    -v $(pwd)/heapdumps:/heapdumps \\
    -v $(pwd)/reports:/reports   \\
    eclipse-mat service
"""

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so submodule imports work
sys.path.insert(0, str(Path(__file__).parent))

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import get_settings
from exceptions import register_exception_handlers
from logging_config import setup_logging
from routes.analysis import router as analysis_router
from routes.operations import router as ops_router


def create_app() -> FastAPI:
    """Application factory — creates and wires the FastAPI instance."""
    settings = get_settings()
    setup_logging()

    application = FastAPI(
        title="MAT Analysis Service",
        description=(
            "REST API for running Eclipse Memory Analyzer Tool (MAT) reports and "
            "analysing the results with Java-specific recommendations.\n\n"
            "**Heap-dump upload workflows:**\n\n"
            "- `POST /analyze/heapdump` — structured **JSON** response (machine-readable).\n"
            "- `POST /analyze/heapdump/report` — **plain-text** report for human reading "
            "  in a terminal (`curl … | cat`). Same pipeline, different output format.\n\n"
            "Set `API_TOKEN` to require `Authorization: Bearer <token>` on these endpoints."
        ),
        version=settings.service_version,
    )

    register_exception_handlers(application)

    if not settings.api_token:
        logging.getLogger("mat-service").warning(
            "API_TOKEN is not set: the analysis endpoints are open to anyone who can reach the service")

    @application.middleware("http")
    async def reject_oversized_uploads(request: Request, call_next):
        """Refuse an upload by its Content-Length before its body is read and spooled to /tmp."""
        length = request.headers.get("content-length")
        if request.method == "POST" and length and length.isdigit() and int(length) > settings.max_upload_size_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"Upload exceeds {settings.max_upload_size_bytes / 1024 ** 3:.0f} GB (MAX_UPLOAD_SIZE_BYTES)"})
        return await call_next(request)
    application.include_router(ops_router)
    application.include_router(analysis_router)

    return application


app = create_app()
