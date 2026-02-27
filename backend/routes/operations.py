"""Operational endpoints: health check and report listing."""

import shutil
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter

from config import get_settings

router = APIRouter(tags=["Operations"])


@router.get("/health")
def health() -> Dict[str, Any]:
    """Liveness probe — returns HTTP 200 when the service is running."""
    settings = get_settings()

    # Disk usage for key volumes
    disk = {}
    for label, path in [("reports", settings.reports_dir), ("heapdumps", settings.heapdumps_dir)]:
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


@router.get("/reports")
def list_reports(reports_dir: str = None) -> Dict[str, Any]:
    """List MAT report ZIP files in *reports_dir*, grouped by report type."""
    if reports_dir is None:
        reports_dir = get_settings().reports_dir

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
