"""The REST service with a stand-in for MAT (see FAKE_MAT in conftest): upload handling, auth, MAT failures,
timeouts, concurrency and cleanup."""

import gzip
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest


def upload(client, content: bytes, name="app.hprof", token=None, path="/analyze/heapdump", **form):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(path, files={"file": (name, content)}, data=form, headers=headers)


def leftovers(dumps):
    """Everything below HEAPDUMPS_DIR except the slot lock files."""
    return [p for p in dumps.rglob("*") if ".mat-slots" not in p.parts]


def test_health_is_open(service):
    make, _ = service
    client = make(API_TOKEN="secret")
    assert client.get("/health").status_code == 200


def test_token_required_when_set(service):
    make, _ = service
    client = make(API_TOKEN="secret")
    assert upload(client, b"static_list").status_code == 401
    assert upload(client, b"static_list", token="wrong").status_code == 401
    assert upload(client, b"static_list", token="secret").status_code == 200


def test_open_without_token(service):
    make, _ = service
    assert upload(make(), b"static_list").status_code == 200


def test_analysis_result_and_cleanup(service, tmp_path):
    make, dumps = service
    args = tmp_path / "args.txt"
    r = upload(make(FAKE_MAT_ARGS=args), b"static_list")
    assert r.status_code == 200
    body = r.json()
    primary = body["suspects"]["analysis"]["primary_suspect"]
    assert primary["class_name"] == "JavaMemoryIssuesDemo"
    assert "STATIC_SESSIONS" in primary["accumulation_point"]["path_text"]
    assert "saved_to" not in body["heapdump"] and "stdout_tail" not in body["mat"]
    assert leftovers(dumps) == []                                 # dump, index files, ZIPs: all gone
    used = args.read_text()
    assert "-vmargs --add-exports=java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED -Xmx2g" in used
    assert "-Duser.language=en -Duser.country=US" in used


def test_gzip_upload(service):
    make, dumps = service
    r = upload(make(), gzip.compress(b"ground_truth"), name="app.hprof.gz")
    assert r.status_code == 200
    threads = [s["thread"] for s in r.json()["suspects"]["analysis"]["significant_suspects"]]
    assert any(t and t.startswith("worker-tl") for t in threads)
    assert leftovers(dumps) == []


def test_rejects_other_files_and_paths_in_names(service):
    make, dumps = service
    client = make()
    assert upload(client, b"x", name="notes.txt").status_code == 400
    r = upload(client, b"static_list", name="../../etc/app.hprof")      # only the base name is used
    assert r.status_code == 200 and r.json()["heapdump"]["filename"] == "app.hprof"


def test_oversized_upload_rejected_before_reading(service):
    make, dumps = service
    r = upload(make(MAX_UPLOAD_SIZE_BYTES=10), b"static_list" * 10)
    assert r.status_code == 413
    assert leftovers(dumps) == []


def test_mat_failure_is_an_error_not_a_stale_report(service):
    make, dumps = service
    client = make()
    assert upload(client, b"static_list").status_code == 200           # leaves nothing behind for the next run
    r = upload(make(FAKE_MAT_EXIT=1), b"ground_truth")
    assert r.status_code == 502
    assert "ran out of memory" in r.json()["detail"]
    assert leftovers(dumps) == []


def test_timeout_kills_mat_and_cleans_up(service, tmp_path):
    make, dumps = service
    pidfile = tmp_path / "mat-child.pid"
    started = time.monotonic()
    r = upload(make(MAT_TIMEOUT_SECONDS=1, FAKE_MAT_PIDFILE=pidfile), b"static_list")
    assert r.status_code == 504
    # killing only the script would leave the JVM running and the request waiting for it (the stand-in: 60 s)
    assert time.monotonic() - started < 15
    child = int(pidfile.read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):                            # the JVM stand-in is gone, not orphaned
        os.kill(child, 0)
    assert leftovers(dumps) == []


def test_concurrent_requests_get_their_own_results(service):
    """Two dumps analysed at the same time must not swap or lose their reports."""
    make, dumps = service
    make(MAT_MAX_CONCURRENT=2, FAKE_MAT_SLEEP=1)
    from fastapi.testclient import TestClient
    from app import create_app
    app = create_app()

    def run(content):
        return upload(TestClient(app), content).json()["suspects"]["analysis"]["primary_suspect"]["class_name"]

    with ThreadPoolExecutor(4) as pool:
        names = list(pool.map(run, [b"static_list", b"ground_truth", b"static_list", b"ground_truth"]))
    assert names == ["JavaMemoryIssuesDemo", "GroundTruth", "JavaMemoryIssuesDemo", "GroundTruth"]
    assert leftovers(dumps) == []


def test_busy_when_no_mat_slot_frees_up(service):
    make, _ = service
    make(MAT_MAX_CONCURRENT=1, MAT_QUEUE_TIMEOUT_SECONDS=0, FAKE_MAT_SLEEP=2)
    from fastapi.testclient import TestClient
    from app import create_app
    app = create_app()
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(r.status_code for r in pool.map(lambda c: upload(TestClient(app), c), [b"static_list", b"static_list"]))
    assert codes == [200, 503]


def test_text_report_sections(service):
    make, _ = service
    r = upload(make(), b"static_list", path="/analyze/heapdump/report", sections="suspects")
    assert r.status_code == 200
    assert "STATIC_SESSIONS" in r.text or "JavaMemoryIssuesDemo" in r.text


@pytest.mark.parametrize("path", ["/analyze/all", "/analyze/suspects", "/analyze/overview", "/analyze/top-components"])
def test_path_based_endpoints_are_gone(service, path):
    make, _ = service
    assert make().post(path, json={"reports_dir": "/etc"}).status_code == 404


def test_reports_listing_is_gone(service):
    make, _ = service
    assert make().get("/reports?reports_dir=/etc").status_code == 404
