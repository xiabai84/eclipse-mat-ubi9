"""Operational endpoints: health check and report listing."""

import shutil
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from config import get_settings

router = APIRouter(tags=["Operations"])


@router.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe — returns HTTP 200 when the service is running."""
    settings = get_settings()

    # Disk usage for key volumes
    disk = {}
    for label, path in [("heapdumps", settings.heapdumps_dir)]:
        try:
            usage = shutil.disk_usage(path)
            disk[label] = {
                "free_gb": round(usage.free / (1024 ** 3), 2),
                "total_gb": round(usage.total / (1024 ** 3), 2),
            }
        except OSError:
            disk[label] = {"free_gb": None, "total_gb": None}

    return {
        "status": "ok",
        "service": settings.service_name,
        "version": settings.service_version,
        "mat_available": Path(settings.mat_script).exists(),
        "disk": disk,
    }
