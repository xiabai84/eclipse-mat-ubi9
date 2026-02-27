"""Unit tests for MATSystemOverviewAnalyzer."""

from pathlib import Path

from analyzers.overview import MATSystemOverviewAnalyzer


def test_analyze_returns_report_data(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()
    data = analyzer.report_data

    assert data["summary"]["total_objects"] > 0
    assert data["summary"]["total_classes"] > 0
    assert data["thread_analysis"]["total_threads"] >= 1


def test_parse_heap_summary(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    s = analyzer.report_data["summary"]
    # 107,347,272 bytes ≈ 102.3 MB
    assert 100 < s["used_heap_mb"] < 110
    assert s["total_objects"] == 1500000
    assert s["total_classes"] == 8500
    assert s["total_classloaders"] == 25
    assert s["total_gc_roots"] == 6200


def test_parse_threads(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    ta = analyzer.report_data["thread_analysis"]
    assert ta["total_threads"] == 2
    names = [t["name"] for t in ta["threads"]]
    assert "main" in names
    assert "GC-Thread" in names


def test_detect_thread_leak(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    # main thread retains 53,477,376 bytes ≈ 51 MB → potential leak (> 50 MB threshold)
    leaks = analyzer.report_data["thread_analysis"]["potential_leaks"]
    assert len(leaks) >= 1
    assert leaks[0]["retained_mb"] > 50


def test_parse_histogram(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    hist = analyzer.report_data["class_histogram"]
    assert len(hist) == 3
    class_names = [c["class"] for c in hist]
    assert "byte[]" in class_names
    assert "java.lang.String" in class_names


def test_detect_problems(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    problem_types = [p["type"] for p in analyzer.report_data["problems"]]
    # 1,500,000 objects → HIGH_OBJECT_COUNT
    assert "HIGH_OBJECT_COUNT" in problem_types
    # 25 classloaders → HIGH_CLASSLOADER_COUNT
    assert "HIGH_CLASSLOADER_COUNT" in problem_types
    # 6,200 GC roots → HIGH_GC_ROOT_COUNT
    assert "HIGH_GC_ROOT_COUNT" in problem_types


def test_generate_report_text(overview_zip: Path):
    analyzer = MATSystemOverviewAnalyzer(str(overview_zip))
    analyzer.analyze()

    report = analyzer.generate_report()
    assert isinstance(report, str)
    assert len(report) > 100
    assert "SYSTEM OVERVIEW MEMORY REPORT" in report
    assert "JVM MEMORY SNAPSHOT" in report
