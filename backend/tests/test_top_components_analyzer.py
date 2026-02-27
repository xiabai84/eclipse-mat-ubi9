"""Unit tests for MATTopComponentsAnalyzer."""

from pathlib import Path

from analyzers.top_components import MATTopComponentsAnalyzer


def test_analyze_returns_report_data(top_components_zip: Path):
    analyzer = MATTopComponentsAnalyzer(str(top_components_zip))
    analyzer.analyze()
    data = analyzer.report_data

    # Should have parsed classloaders and consumers
    assert len(data["classloaders"]) >= 1
    assert len(data["top_consumers"]) >= 1
    assert data["summary"]["total_heap_mb"] > 0


def test_parse_classloaders(top_components_zip: Path):
    analyzer = MATTopComponentsAnalyzer(str(top_components_zip))
    analyzer.analyze()

    classloaders = analyzer.report_data["classloaders"]
    names = [cl["name"] for cl in classloaders]
    assert any("AppClassLoader" in n for n in names)
    # AppClassLoader retains 85,877,818 bytes ≈ 81.9 MB
    app_cl = next(cl for cl in classloaders if "AppClassLoader" in cl["name"])
    assert app_cl["retained_mb"] > 70


def test_parse_top_consumers(top_components_zip: Path):
    analyzer = MATTopComponentsAnalyzer(str(top_components_zip))
    analyzer.analyze()

    consumers = analyzer.report_data["top_consumers"]
    names = [c["name"] for c in consumers]
    assert any("LeakyCache" in n for n in names)


def test_generate_report_text(top_components_zip: Path):
    analyzer = MATTopComponentsAnalyzer(str(top_components_zip))
    analyzer.analyze()

    report = analyzer.generate_report()
    assert isinstance(report, str)
    assert len(report) > 100
    assert "TOP COMPONENTS MEMORY REPORT" in report
    assert "HEAP OVERVIEW" in report
