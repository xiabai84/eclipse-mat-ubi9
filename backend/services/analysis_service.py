"""Analysis orchestration: upload → (decompress) → Eclipse MAT → the three analyzers → cleanup.

Every request works in its own directory below HEAPDUMPS_DIR. The directory - uploaded dump, MAT index files,
report ZIPs, extracted HTML - is removed when the request ends, also when it fails.
"""

import asyncio
import gzip
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException, UploadFile, status

from analyzers import (
    MATLeakSuspectsAnalyzer,
    MATSystemOverviewAnalyzer,
    MATTopComponentsAnalyzer,
)
from config import get_settings
from services.mat_runner import MatBusy, find_report, mat_slot, run_mat

logger = logging.getLogger("mat-service")

ANALYZERS = {
    "suspects": MATLeakSuspectsAnalyzer,
    "overview": MATSystemOverviewAnalyzer,
    "top_components": MATTopComponentsAnalyzer,
}
CHUNK = 1024 * 1024


def run_all_analyzers(work_dir: Path, include_text: bool = True) -> Dict[str, Any]:
    """Run the three analyzers on the report ZIPs in ``work_dir``."""
    result: Dict[str, Any] = {key: None for key in ANALYZERS}
    result["total_problems"] = 0
    for key, cls in ANALYZERS.items():
        report_zip = find_report(work_dir, key)
        if report_zip is None:
            result[key] = {"status": "skipped", "reason": "MAT produced no report of this kind"}
            continue
        try:
            analyzer = cls(str(report_zip), str(work_dir / f"extracted_{key}"))
            analyzer.analyze()
            entry: Dict[str, Any] = {
                "status": "ok",
                "problems_found": len(analyzer.report_data.get("problems", [])),
                "analysis": analyzer.report_data,
            }
            if include_text:
                entry["report_text"] = analyzer.generate_report()
            result[key] = entry
            result["total_problems"] += entry["problems_found"]
        except Exception as exc:
            logger.exception("Analyzer %s failed", key)
            result[key] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    return result


def _decompress(src: Path, dest: Path, limit: int) -> None:
    """gunzip src → dest, refusing to write more than ``limit`` bytes (decompression bomb)."""
    written = 0
    with gzip.open(src, "rb") as fin, dest.open("wb") as fout:
        while True:
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise ValueError(f"decompressed dump exceeds {limit / 1024 ** 3:.0f} GB (MAX_DUMP_SIZE_BYTES)")
            fout.write(chunk)


def _mat_locked(dump: Path) -> Dict[str, Any]:
    with mat_slot():
        return run_mat(dump)


async def heapdump_pipeline(file: UploadFile, include_text: bool = True) -> tuple:
    """
    Save the uploaded .hprof / .hprof.gz, run MAT and the analyzers, and return
    ``(filename, size_mb, mat_result, analysis)``. Raises HTTPException on failure.
    """
    settings = get_settings()
    filename = Path(file.filename or "dump.hprof").name        # never a path from the client
    lower = filename.lower()
    if not (lower.endswith(".hprof") or lower.endswith(".hprof.gz")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .hprof and .hprof.gz heap dumps are accepted.")

    work = Path(settings.heapdumps_dir) / f"job-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    loop = asyncio.get_running_loop()
    try:
        # ── save the upload in 1 MB chunks ───────────────────────────────────
        upload = work / ("upload.hprof.gz" if lower.endswith(".gz") else "heap.hprof")
        total = 0
        with upload.open("wb") as fh:
            while chunk := await file.read(CHUNK):
                total += len(chunk)
                if total > settings.max_upload_size_bytes:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"Upload exceeds {settings.max_upload_size_bytes / 1024 ** 3:.0f} GB (MAX_UPLOAD_SIZE_BYTES)")
                await loop.run_in_executor(None, fh.write, chunk)
        await file.close()
        size_mb = round(total / 1_048_576, 2)
        logger.info("Saved %s (%.1f MB) in %s", filename, size_mb, work.name)

        dump = work / "heap.hprof"
        if upload != dump:
            try:
                await loop.run_in_executor(None, _decompress, upload, dump, settings.max_dump_size_bytes)
            except (OSError, EOFError, ValueError) as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Cannot decompress {filename}: {exc}")
            upload.unlink()

        # ── Eclipse MAT (waits for a free slot) ──────────────────────────────
        try:
            mat_result = await loop.run_in_executor(None, _mat_locked, dump)
        except MatBusy as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"MAT is busy: {exc}", headers={"Retry-After": "300"})
        if mat_result["status"] != "ok":
            detail = mat_result["error"]
            if mat_result.get("output_tail"):
                detail += "\n" + mat_result["output_tail"]
            code = status.HTTP_504_GATEWAY_TIMEOUT if "timed out" in detail else status.HTTP_502_BAD_GATEWAY
            raise HTTPException(code, detail)

        # ── analyzers ─────────────────────────────────────────────────────────
        analysis = await loop.run_in_executor(None, run_all_analyzers, work, include_text)
        return filename, size_mb, mat_result, analysis
    finally:
        shutil.rmtree(work, ignore_errors=True)
        logger.info("Removed work directory %s", work.name)
