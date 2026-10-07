"""Eclipse MAT subprocess runner.

Runs ParseHeapDump.sh on a dump inside its own work directory: MAT writes its index files and the three report
ZIPs next to the dump, so concurrent requests never see each other's files. At most ``mat_max_concurrent`` MAT
runs happen at the same time across all worker processes (file-lock slots).
"""

import fcntl
import logging
import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from config import get_settings

logger = logging.getLogger("mat-service")

REPORTS = {
    "suspects": "org.eclipse.mat.api:suspects",
    "overview": "org.eclipse.mat.api:overview",
    "top_components": "org.eclipse.mat.api:top_components",
}
REPORT_SUFFIX = {"suspects": "_Leak_Suspects.zip", "overview": "_System_Overview.zip", "top_components": "_Top_Components.zip"}


class MatBusy(Exception):
    """No MAT slot became free within mat_queue_timeout_seconds."""


def find_report(work_dir: Path, key: str) -> Optional[Path]:
    """The report ZIP of one kind in a work directory."""
    matches = sorted(work_dir.glob(f"*{REPORT_SUFFIX[key]}"))
    return matches[0] if matches else None


def container_memory_limit() -> Optional[int]:
    """The cgroup memory limit in bytes (v2 or v1), or None when unlimited / unknown."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw.isdigit() and int(raw) < 1 << 60:
            return int(raw)
    return None


def mat_heap() -> str:
    """-Xmx for MAT: configured, or 75 % of the container limit shared by the concurrent MAT runs."""
    settings = get_settings()
    if settings.mat_xmx:
        return settings.mat_xmx
    limit = container_memory_limit()
    if not limit:
        return "4g"
    mb = int(limit * 0.75 / max(1, settings.mat_max_concurrent) / 1_048_576)
    return f"{max(mb, 512)}m"


@contextmanager
def mat_slot() -> Iterator[None]:
    """Hold one of mat_max_concurrent slots (lock files shared by all worker processes) while MAT runs."""
    settings = get_settings()
    slot_dir = Path(settings.heapdumps_dir) / ".mat-slots"
    slot_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + settings.mat_queue_timeout_seconds
    while True:
        for i in range(max(1, settings.mat_max_concurrent)):
            fh = open(slot_dir / f"slot-{i}.lock", "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fh.close()
                continue
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            return
        if time.monotonic() > deadline:
            raise MatBusy(f"no MAT slot free within {settings.mat_queue_timeout_seconds}s")
        time.sleep(1)


def mat_command(dump: Path) -> List[str]:
    """ParseHeapDump.sh with the three reports and our JVM options.

    -vmargs on the command line *replaces* the -vmargs of MemoryAnalyzer.ini, so the ini's --add-exports is
    repeated. An English locale makes MAT print "81.8 MB", not "81,8 MB" - the analyzers parse both, but one
    format is less error-prone.
    """
    settings = get_settings()
    return [
        settings.mat_script, str(dump), *REPORTS.values(),
        "-vmargs",
        "--add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED",
        f"-Xmx{mat_heap()}",
        "-Duser.language=en", "-Duser.country=US",
    ]


def run_mat(dump: Path) -> Dict[str, Any]:
    """Run MAT on ``dump``; the reports land next to it. status "ok" only when all three reports exist."""
    settings = get_settings()
    if not Path(settings.mat_script).exists():
        return {"status": "error", "error": f"Eclipse MAT not found at {settings.mat_script}"}

    cmd = mat_command(dump)
    logger.info("Running MAT (-Xmx%s) on %s", mat_heap(), dump.name)
    started = time.monotonic()
    # own process group: on timeout the MAT JVM is killed too, not only the shell script that started it
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=str(dump.parent), start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=settings.mat_timeout_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return {"status": "error",
                "error": f"MAT timed out after {settings.mat_timeout_seconds}s (MAT_TIMEOUT)"}
    duration = round(time.monotonic() - started, 1)

    missing = [k for k in REPORTS if find_report(dump.parent, k) is None]
    if proc.returncode != 0 or missing:
        tail = (output or "").strip().splitlines()[-15:]
        hint = " - MAT ran out of memory, raise MAT_XMX / the memory limit" if "OutOfMemoryError" in (output or "") else ""
        return {"status": "error", "returncode": proc.returncode,
                "error": f"MAT failed (exit {proc.returncode}, missing reports: {', '.join(missing) or 'none'}){hint}",
                "output_tail": "\n".join(tail)}
    return {"status": "ok", "returncode": 0, "duration_s": duration}
