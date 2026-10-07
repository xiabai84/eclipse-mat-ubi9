"""The human-readable reports render for every real MAT fixture (they format the parsed values)."""

import pytest

from analyzers import MATLeakSuspectsAnalyzer, MATSystemOverviewAnalyzer, MATTopComponentsAnalyzer

KINDS = [(MATLeakSuspectsAnalyzer, "Leak_Suspects"), (MATSystemOverviewAnalyzer, "System_Overview"),
         (MATTopComponentsAnalyzer, "Top_Components")]


@pytest.mark.parametrize("fixture", ["static_list", "small_heap", "ground_truth"])
@pytest.mark.parametrize("cls, kind", KINDS)
def test_report_renders(mat_zip, fixture, cls, kind):
    text = cls(str(mat_zip(fixture, kind))).analyze().generate_report()
    assert len(text) > 200
