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

from fastapi import FastAPI

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
            "**Pre-generated report ZIPs** (`POST /analyze/suspects` etc.) — analyse "
            "existing MAT report ZIPs that you have already generated."
        ),
        version=settings.service_version,
    )

    register_exception_handlers(application)
    application.include_router(ops_router)
    application.include_router(analysis_router)

    return application


app = create_app()
