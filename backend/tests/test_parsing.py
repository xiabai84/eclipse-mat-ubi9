"""Size and name parsing as MAT prints them - English and European locales."""

import pytest

from analyzers.base import MATBaseAnalyzer


class _Parser(MATBaseAnalyzer):
    def __init__(self):
        pass

    def parse_report(self):
        pass

    def generate_report(self):
        return ""


P = _Parser()
MB = 1_048_576


@pytest.mark.parametrize("raw, mb", [
    ("81.8 MB", 81.8), ("81,8 MB", 81.8),                 # MAT under an English / a German JVM locale
    ("1.234,5 MB", 1234.5), ("1,234.5 MB", 1234.5), ("1,024 MB", 1024),
    ("1.5 GB", 1536), ("512 KB", 0.5), ("2 MB", 2),
    ("104,889,144", 104_889_144 / MB), ("83.892.400", 83_892_400 / MB),   # raw byte counts, both locales
    (">= 84,487,384", 84_487_384 / MB),                   # MAT's lower bound in histograms (was parsed as 0)
    ("40 B", 40 / MB), ("83,892,400 (97.78%) bytes", 83_892_400 / MB),
    ("", 0.0), ("n/a", 0.0),
])
def test_sizes(raw, mb):
    assert P._parse_size_to_mb(raw) == pytest.approx(mb)


@pytest.mark.parametrize("raw, name", [
    ("byte[]All objects", "byte[]"),
    ("java.util.ArrayList All 95 objects", "java.util.ArrayList"),
    ("java.lang.String First 10 of 8,523 objects", "java.lang.String"),
    ("+ JavaMemoryIssuesDemo Only object", "JavaMemoryIssuesDemo"),
    (".\\ STATIC_SESSIONS class JavaMemoryIssuesDemo @ 0x7efd115e0", "STATIC_SESSIONS class JavaMemoryIssuesDemo @ 0x7efd115e0"),
    ("<system class loader>", "<system class loader>"),
])
def test_names(raw, name):
    assert P._clean_name(raw) == name


def test_text_keeps_angle_brackets_unless_raw_html():
    assert P._clean_text("loaded by <system class loader> , which") == "loaded by <system class loader> , which"
    assert P._clean_text("<b>x</b> loaded by &lt;system class loader&gt;", raw_html=True) == "x loaded by <system class loader>"
