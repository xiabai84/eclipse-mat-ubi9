"""Leak Suspects analyzer against real MAT reports."""

import json

import pytest

from analyzers import MATLeakSuspectsAnalyzer


def analyze(mat_zip, name):
    return MATLeakSuspectsAnalyzer(str(mat_zip(name, "Leak_Suspects"))).analyze().report_data


def test_static_list(mat_zip):
    d = analyze(mat_zip, "static_list")
    assert d["summary"]["total_heap_mb"] == pytest.approx(81.8)
    assert d["summary"]["leak_suspects_count"] == 1
    p = d["primary_suspect"]
    assert p["class_name"] == "JavaMemoryIssuesDemo"
    assert p["retained_mb"] == pytest.approx(83_892_400 / 1_048_576)
    assert p["heap_pct"] == pytest.approx(97.78)
    # the field that holds the memory - the key information
    acc = p["accumulation_point"]
    assert acc["path_text"] == "JavaMemoryIssuesDemo.STATIC_SESSIONS (static) → java.util.ArrayList.elementData → java.lang.Object[]"
    assert acc["retained_mb"] == pytest.approx(83_889_736 / 1_048_576)
    assert [x["type"] for x in d["problems"]] == ["PRIMARY_LEAK", "SIGNIFICANT_LEAK_RATIO"]


def test_ground_truth_cache_and_threadlocal(mat_zip):
    d = analyze(mat_zip, "ground_truth")
    suspects = [d["primary_suspect"]] + d["significant_suspects"] + d["other_suspects"]
    cache = next(s for s in suspects if s["class_name"] == "GroundTruth")
    # cache, strings and lists live in several static fields of GroundTruth: MAT names the class itself
    assert cache["accumulation_point"]["path_text"] == "GroundTruth (static fields)"
    tl = next(s for s in suspects if s["thread"] and s["thread"].startswith("worker-tl"))
    assert tl["retained_mb"] == pytest.approx(60.0, abs=0.1)
    assert tl["accumulation_point"]["path_text"].startswith("java.lang.Thread.threadLocals → java.lang.ThreadLocal$ThreadLocalMap.table")
    # MAT: "The thread ... main keeps local variables with total size 5,672 bytes" - named, but with its size,
    # so nobody mistakes it for the holder of the 130 MB
    assert cache["thread"] == "main (keeps local variables of 0.0 MB)"


def test_small_heap_has_no_problems(mat_zip):
    """MAT names suspects on every heap; 0.6 MB on a 2 MB heap is not a leak."""
    d = analyze(mat_zip, "small_heap")
    assert d["summary"]["leak_suspects_count"] == 2
    assert d["problems"] == []


def test_descriptions_keep_system_class_loader(mat_zip):
    d = analyze(mat_zip, "small_heap")
    assert "loaded by <system class loader>" in d["primary_suspect"]["description"]


def test_no_heap_string_contents_in_result(mat_zip):
    """MAT pages show String values next to references; the result must not carry them."""
    from pathlib import Path
    pages = (Path(__file__).parent / "fixtures" / "mat" / "small_heap_Leak_Suspects").rglob("*.html")
    assert any("USER_NAME" in f.read_text() for f in pages)        # the MAT page does contain one
    d = analyze(mat_zip, "small_heap")
    assert "USER_NAME" not in json.dumps(d)
