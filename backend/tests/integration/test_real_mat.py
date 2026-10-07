"""End to end with the real Eclipse MAT: builds a heap dump with known content (GroundTruth.java), uploads it,
and checks what the service reports. Runs when MAT_SCRIPT points to a real ParseHeapDump.sh (Linux; CI job
"integration"), otherwise it is skipped.

    MAT_SCRIPT=/opt/eclipse-mat/ParseHeapDump.sh pytest tests/integration
"""

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MAT = os.environ.get("MAT_SCRIPT", "")
pytestmark = pytest.mark.skipif(
    not (MAT and Path(MAT).exists() and shutil.which("java")), reason="needs a real MAT (MAT_SCRIPT) and java")

HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def dump(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("dump") / "gt.hprof"
    subprocess.run(["java", "-Xmx1g", str(HERE / "GroundTruth.java"), str(path)], check=True, timeout=300)
    return gzip.compress(path.read_bytes(), compresslevel=1)        # as jvm-diag delivers it: .hprof.gz


@pytest.fixture
def client(tmp_path, monkeypatch):
    import config
    from fastapi.testclient import TestClient

    def make(**env):
        monkeypatch.setenv("HEAPDUMPS_DIR", str(tmp_path / "heapdumps"))
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        config.get_settings.cache_clear()
        from app import create_app
        return TestClient(create_app())

    yield make
    config.get_settings.cache_clear()


def test_ground_truth(client, dump):
    r = client(MAT_XMX="2g").post("/analyze/heapdump", files={"file": ("gt.hprof.gz", dump)})
    assert r.status_code == 200, r.text
    body = r.json()

    suspects = body["suspects"]["analysis"]
    all_suspects = [suspects["primary_suspect"]] + suspects["significant_suspects"] + suspects["other_suspects"]
    assert suspects["primary_suspect"]["class_name"] == "GroundTruth"
    tl = next(s for s in all_suspects if (s["thread"] or "").startswith("worker-tl"))
    assert tl["retained_mb"] == pytest.approx(60, abs=0.5)
    assert "threadLocals" in tl["accumulation_point"]["path_text"]

    overview = body["overview"]["analysis"]
    assert [x["thread"] for x in overview["thread_analysis"]["potential_leaks"]] == ["worker-tl"]
    assert "LARGE_ARRAYS" in {p["type"] for p in overview["problems"]}

    top = body["top_components"]["analysis"]
    assert top["waste_analysis"]["empty_collections"]["count"] >= 200_000
    assert top["summary"]["total_heap_mb"] == pytest.approx(overview["summary"]["used_heap_mb"], rel=0.02)
    assert "customer-status" not in r.text                       # no heap string contents in the response


def test_mat_heap_setting_reaches_mat(client, dump):
    """-vmargs on the command line replaces MemoryAnalyzer.ini's: a tiny -Xmx must make MAT fail."""
    r = client(MAT_XMX="32m").post("/analyze/heapdump", files={"file": ("gt.hprof.gz", dump)})
    assert r.status_code == 502
    assert "out of memory" in r.json()["detail"].lower() or "OutOfMemoryError" in r.json()["detail"]
