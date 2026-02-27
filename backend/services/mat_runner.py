"""Eclipse MAT subprocess runner.

Executes ParseHeapDump.sh and manages the generated report ZIPs.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import get_settings

logger = logging.getLogger("mat-service")


def find_report(reports_dir: Path, suffix_patterns: List[str]) -> Optional[Path]:
    """Return the first ZIP whose name contains one of the suffix patterns."""
    for pattern in suffix_patterns:
        matches = list(reports_dir.glob(f"*{pattern}*.zip"))
        if matches:
            return matches[0]
    return None


def run_mat(heapdump_path: Path, reports_dir: Path) -> Dict[str, Any]:
    """
    Execute Eclipse MAT ParseHeapDump.sh to generate all three report ZIPs.
    Returns a result dict with status, tail of stdout/stderr, and zip paths.
    """
    settings = get_settings()

    if not Path(settings.mat_script).exists():
        return {
            "status": "error",
            "error": (
                f"Eclipse MAT not found at {settings.mat_script}. "
                "Build the Docker image using the provided Dockerfile."
            ),
        }

    reports_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        settings.mat_script,
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
            timeout=settings.mat_timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "error": (
                f"MAT timed out after {settings.mat_timeout_seconds}s. "
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
