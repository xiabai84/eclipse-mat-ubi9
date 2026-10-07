"""System Overview analyzer against real MAT reports."""

import pytest

from analyzers import MATSystemOverviewAnalyzer


def analyze(mat_zip, name):
    return MATSystemOverviewAnalyzer(str(mat_zip(name, "System_Overview"))).analyze().report_data


def test_summary(mat_zip):
    s = analyze(mat_zip, "static_list")["summary"]
    assert s["used_heap_mb"] == pytest.approx(81.8)
    assert (s["total_objects"], s["total_classes"], s["total_classloaders"], s["total_gc_roots"]) == (46871, 1775, 5, 1690)


def test_histogram_names_and_retained_sizes(mat_zip):
    h = {row["class"]: row for row in analyze(mat_zip, "static_list")["class_histogram"]}
    # names without MAT's "All objects" link text, retained from ">= 84,487,384" (was 0.0 for every row)
    assert h["byte[]"]["objects"] == 11677
    assert h["byte[]"]["retained_mb"] == pytest.approx(84_487_384 / 1_048_576)
    assert h["JavaMemoryIssuesDemo$LeakedSession"]["objects"] == 80
    assert all("All objects" not in name for name in h)


def test_ground_truth_findings(mat_zip):
    d = analyze(mat_zip, "ground_truth")
    types = [p["type"] for p in d["problems"]]
    assert "THREAD_LEAK" in types                      # worker-tl keeps 60 MB in a ThreadLocal
    assert "LARGE_ARRAYS" in types                     # byte[] retains > 100 MB - could never fire before
    leak = d["thread_analysis"]["potential_leaks"]
    assert [(x["thread"], round(x["retained_mb"])) for x in leak] == [("worker-tl", 60)]


def test_object_count_and_gc_roots_are_no_problems():
    from analyzers.overview import MATSystemOverviewAnalyzer as A
    a = A.__new__(A)
    a.report_data = {"summary": {"used_heap_mb": 100.0, "total_objects": 12_000_000, "total_classes": 1,
                                 "total_classloaders": 1, "total_gc_roots": 9000},
                     "thread_analysis": {"potential_leaks": []}, "class_histogram": [], "top_consumers": [],
                     "problems": [], "warnings": []}
    a._analyze_problems()
    assert a.report_data["problems"] == []
    assert {w["type"] for w in a.report_data["warnings"]} == {"HIGH_OBJECT_COUNT", "HIGH_GC_ROOT_COUNT"}
