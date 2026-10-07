"""Top Components analyzer against real MAT reports."""

import json

import pytest

from analyzers import MATTopComponentsAnalyzer


def analyze(mat_zip, name):
    return MATTopComponentsAnalyzer(str(mat_zip(name, "Top_Components"))).analyze().report_data


def test_components_and_total(mat_zip):
    d = analyze(mat_zip, "ground_truth")
    assert d["summary"]["components_analyzed"] == 2
    # derived from the component shares; the System Overview of the same dump says 270.4 MB
    assert d["summary"]["total_heap_mb"] == pytest.approx(270.4, rel=0.01)
    assert sum(c["heap_pct"] for c in d["classloaders"]) == 100
    assert d["classloaders"][0]["name"].startswith("com.sun.tools.javac.launcher.MemoryClassLoader")


def test_top_consumers_are_named_objects_without_duplicates(mat_zip):
    d = analyze(mat_zip, "ground_truth")
    names = [c["name"] for c in d["top_consumers"]]
    assert names[:2] == ["class GroundTruth", "java.lang.Thread"]
    assert d["top_consumers"][0]["size_mb"] == pytest.approx(207.5, abs=0.1)
    assert d["top_consumers"][1]["size_mb"] == pytest.approx(60.0, abs=0.1)
    assert len(names) == len(set(names))
    assert not any("objects" in n or n.startswith(("+", "<all>")) for n in names)
    # one problem per object, not one per level of MAT's tree
    assert [p["type"] for p in d["problems"]].count("DOMINANT_CONSUMER") == 1


def test_waste_uses_mat_verdicts(mat_zip):
    d = analyze(mat_zip, "ground_truth")
    w = d["waste_analysis"]
    assert w["empty_collections"]["wasted_mb"] == pytest.approx(59_202_952 / 1_048_576)
    assert w["empty_collections"]["count"] == 200_119
    # low fill ratios: memory *retained* by those collections (the 60 MB ThreadLocal), not waste
    assert w["collection_fill_ratios"]["wasted_mb"] == 0
    assert w["collection_fill_ratios"]["retained_mb"] == pytest.approx(60.0, abs=0.1)
    problems = {p["type"] for p in d["problems"]}
    assert "EMPTY_COLLECTIONS" in problems
    assert not problems & {"LOW_FILL_COLLECTIONS", "LOW_FILL_ARRAYS"}
    assert {"LOW_FILL_COLLECTIONS", "LOW_FILL_ARRAYS"} <= {x["type"] for x in d["warnings"]}


def test_no_heap_string_contents(mat_zip):
    """MAT's duplicate-strings verdict lists the strings ("95 × customer-status-ACTIVE") - not in the result."""
    d = analyze(mat_zip, "ground_truth")
    assert d["waste_analysis"]["duplicate_strings"]["count"] > 0
    assert "customer-status" not in json.dumps(d)


def test_small_heap_has_no_problems(mat_zip):
    assert analyze(mat_zip, "small_heap")["problems"] == []


def test_component_counts_are_numbers(mat_zip):
    cl = analyze(mat_zip, "ground_truth")["classloaders"]
    assert all(isinstance(c["objects"], int) and isinstance(c["classes"], int) for c in cl)
    assert max(c["objects"] for c in cl) >= 300_000                 # "300.5k" on MAT's page
