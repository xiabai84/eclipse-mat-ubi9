"""Unit tests for MATLeakSuspectsAnalyzer."""

from pathlib import Path

from analyzers.suspects import MATLeakSuspectsAnalyzer


def test_analyze_returns_report_data(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()
    data = analyzer.report_data

    assert data["summary"]["leak_suspects_count"] >= 1
    assert data["primary_suspect"] is not None
    assert data["primary_suspect"]["class_name"] == "com.example.LeakyCache"


def test_parse_suspects_count(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()

    assert analyzer.report_data["summary"]["leak_suspects_count"] == 2


def test_parse_total_heap(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()

    total_mb = analyzer.report_data["summary"]["total_heap_mb"]
    # 107,347,272 bytes ≈ 102.3 MB
    assert 100 < total_mb < 110


def test_parse_retained_sizes(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()

    primary = analyzer.report_data["primary_suspect"]
    # 104,889,144 bytes ≈ 100.0 MB
    assert primary["retained_mb"] > 90


def test_identify_problems(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()

    problem_types = [p["type"] for p in analyzer.report_data["problems"]]
    assert "PRIMARY_LEAK" in problem_types
    # 97.71% heap consumed → should trigger SIGNIFICANT_LEAK_RATIO
    assert "SIGNIFICANT_LEAK_RATIO" in problem_types


def test_generate_report_text(suspects_zip: Path):
    analyzer = MATLeakSuspectsAnalyzer(str(suspects_zip))
    analyzer.analyze()

    report = analyzer.generate_report()
    assert isinstance(report, str)
    assert len(report) > 100
    assert "MEMORY LEAK SUSPECTS REPORT" in report
    assert "HEAP OVERVIEW" in report
    assert "PRIMARY LEAK SUSPECT" in report
    assert "com.example.LeakyCache" in report
