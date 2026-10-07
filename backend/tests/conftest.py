"""Shared fixtures.

The analyzer fixtures in fixtures/mat/ are real Eclipse MAT 1.16.1 reports (reduced and scrubbed by
fixtures/make_fixtures.py), stored as HTML text and zipped at test time - no binary test data in the repo:

  static_list   demo scenario 1: a static List<LeakedSession> holding 80 MB (97.78 % of an 81.8 MB heap)
  small_heap    a healthy 2 MB heap - MAT still names suspects, none of them is a problem
  ground_truth  tests/integration/GroundTruth.java: 120 MB cache in PRODUCT_CACHE, 60 MB in a ThreadLocal of the
                live thread "worker-tl", 200,000 empty ArrayLists (56.5 MB), equal strings
"""

import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).parent / "fixtures" / "mat"
KINDS = ("Leak_Suspects", "System_Overview", "Top_Components")


def zip_fixture(name: str, kind: str, dest_dir: Path, prefix: str = None) -> Path:
    """Zip fixtures/mat/<name>_<kind>/ into dest_dir/<prefix or name>_<kind>.zip, as MAT writes it."""
    src = FIXTURES / f"{name}_{kind}"
    dest = dest_dir / f"{prefix or name}_{kind}.zip"
    with zipfile.ZipFile(dest, "w") as z:
        for f in sorted(src.rglob("*.html")):
            z.write(f, f.relative_to(src).as_posix())
    return dest


@pytest.fixture
def mat_zip(tmp_path):
    """mat_zip("ground_truth", "Leak_Suspects") -> path of a real MAT report ZIP."""
    return lambda name, kind: zip_fixture(name, kind, tmp_path)


FAKE_MAT = """#!/bin/sh
# Stand-in for ParseHeapDump.sh: the dump's content names the fixture set to "produce".
dump="$1"; dir=$(dirname "$dump")
[ -n "$FAKE_MAT_ARGS" ] && echo "$@" > "$FAKE_MAT_ARGS"
[ -n "$FAKE_MAT_PIDFILE" ] && { sleep 60 & echo $! > "$FAKE_MAT_PIDFILE"; wait; }
[ -n "$FAKE_MAT_SLEEP" ] && sleep "$FAKE_MAT_SLEEP"
[ "${FAKE_MAT_EXIT:-0}" != 0 ] && { echo "java.lang.OutOfMemoryError: Java heap space"; exit "$FAKE_MAT_EXIT"; }
set=$(head -c 40 "$dump" | tr -dc 'a-z_')
for kind in Leak_Suspects System_Overview Top_Components; do
  cp "$FAKE_MAT_FIXTURES/${set}_$kind.zip" "$dir/heap_$kind.zip"
done
"""


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A fresh app with a fake MAT; returns (client factory, heapdumps dir)."""
    from fastapi.testclient import TestClient

    import config

    zips = tmp_path / "fixture-zips"
    zips.mkdir()
    for name in ("static_list", "small_heap", "ground_truth"):
        for kind in KINDS:
            zip_fixture(name, kind, zips)
    script = tmp_path / "ParseHeapDump.sh"
    script.write_text(FAKE_MAT)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    dumps = tmp_path / "heapdumps"

    monkeypatch.setenv("HEAPDUMPS_DIR", str(dumps))
    monkeypatch.setenv("MAT_SCRIPT", str(script))
    monkeypatch.setenv("MAT_XMX", "2g")
    monkeypatch.setenv("FAKE_MAT_FIXTURES", str(zips))

    def make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        config.get_settings.cache_clear()
        from app import create_app
        return TestClient(create_app())

    yield make, dumps
    config.get_settings.cache_clear()
