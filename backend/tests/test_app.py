"""Unit tests for app.py — helpers and routes."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from starlette.testclient import TestClient

# Import after sys.path is set up by conftest
from app import app, _find_report, _resolve_output, _run_analyzer
from analyzers import MATLeakSuspectsAnalyzer


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestFindReport:
    def test_find_report_matches(self, tmp_path: Path):
        (tmp_path / "app_Leak_Suspects.zip").touch()
        result = _find_report(tmp_path, ["Leak_Suspects", "Suspects"])
        assert result is not None
        assert "Leak_Suspects" in result.name

    def test_find_report_no_match(self, tmp_path: Path):
        (tmp_path / "unrelated.zip").touch()
        result = _find_report(tmp_path, ["Leak_Suspects", "Suspects"])
        assert result is None


class TestResolveOutput:
    def test_resolve_output_with_dir(self, tmp_path: Path):
        out = tmp_path / "custom_out"
        result = _resolve_output(str(out), "test")
        assert result == str(out)
        assert out.exists()

    def test_resolve_output_none(self):
        result = _resolve_output(None, "test")
        assert "mat_test_" in result
        # Clean up temp dir
        shutil.rmtree(result, ignore_errors=True)


class TestRunAnalyzer:
    def test_run_analyzer_file_not_found(self):
        from app import AnalyzeRequest
        from fastapi import HTTPException
        req = AnalyzeRequest(report_path="/nonexistent/path.zip")
        with pytest.raises(HTTPException) as exc_info:
            _run_analyzer(MATLeakSuspectsAnalyzer, req)
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_endpoint(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "mat-analysis"
        assert "version" in data


class TestReportsEndpoint:
    def test_reports_endpoint(self, client: TestClient):
        resp = client.get("/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert "reports" in data

    def test_reports_missing_dir(self, client: TestClient):
        resp = client.get("/reports?reports_dir=/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert "not found" in data.get("note", "").lower() or data["reports"] == {}


class TestAnalyzeSuspectsRoute:
    def test_analyze_suspects_success(self, client: TestClient, suspects_zip: Path):
        resp = client.post(
            "/analyze/suspects",
            json={"report_path": str(suspects_zip), "include_text": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["problems_found"] >= 1

    def test_analyze_suspects_404(self, client: TestClient):
        resp = client.post(
            "/analyze/suspects",
            json={"report_path": "/nonexistent.zip"},
        )
        assert resp.status_code == 404


class TestAnalyzeAllRoute:
    def test_analyze_all_success(
        self, client: TestClient, reports_dir_with_zips: Path
    ):
        resp = client.post(
            "/analyze/all",
            json={
                "reports_dir": str(reports_dir_with_zips),
                "include_text": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["suspects"] is not None
        assert data["suspects"]["status"] == "ok"
        assert data["overview"]["status"] == "ok"
        assert data["top_components"]["status"] == "ok"

    def test_analyze_all_missing_dir(self, client: TestClient):
        resp = client.post(
            "/analyze/all",
            json={"reports_dir": "/does/not/exist"},
        )
        assert resp.status_code == 404


class TestHeapdumpRoutes:
    def test_heapdump_rejects_non_hprof(self, client: TestClient):
        """Upload a non-.hprof file → 400."""
        resp = client.post(
            "/analyze/heapdump",
            files={"file": ("dump.txt", b"not a heap dump", "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "hprof" in resp.json()["detail"].lower()
