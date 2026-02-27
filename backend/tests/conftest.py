"""Shared fixtures for MAT analyzer and app tests.

All ZIP fixtures are created in-memory — no binary test data checked into the repo.
"""

import io
import sys
import zipfile
from pathlib import Path
from typing import Generator

import pytest

# Ensure backend/ is on sys.path so `from analyzers import ...` works
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Minimal HTML payloads that satisfy each analyzer's parser
# ---------------------------------------------------------------------------

SUSPECTS_INDEX_HTML = """\
<html><body>
<img alt="Pie chart of memory usage. Total: 107,347,272" />
<map>
  <area alt="Slice (a)  Problem Suspect 1: Shallow Size: 40 B  Retained Size: 104,889,144" />
  <area alt="Slice (b)  Problem Suspect 2: Shallow Size: 32 B  Retained Size: 1,234,567" />
</map>
<div class="important">
  <p>Problem Suspect 1</p>
  <p>One instance of <q>com.example.LeakyCache</q> loaded by
  <q>sun.misc.Launcher$AppClassLoader</q>
  occupies 104,889,144 (97.71%) bytes.</p>
</div>
<div class="important">
  <p>Problem Suspect 2</p>
  <p>128 instances of <q>com.example.SmallObject</q> loaded by
  <q>sun.misc.Launcher$AppClassLoader</q>
  occupy 1,234,567 (1.15%) bytes.</p>
</div>
</body></html>
"""

SUSPECTS_DETAIL_HTML = """\
<html><body>
<h2>Problem Suspect 1</h2>
<div class="important">
  <p>One instance of <q>com.example.LeakyCache</q> loaded by
  <q>sun.misc.Launcher$AppClassLoader</q>
  occupies 104,889,144 (97.71%) bytes.</p>
</div>
<table>
  <tr><th>Class Name</th><th>Shallow Heap</th><th>Retained Heap</th></tr>
  <tr><td>java.util.HashMap</td><td>48</td><td>52428800</td></tr>
  <tr><td>java.lang.Object[]</td><td>1024</td><td>26214400</td></tr>
</table>
</body></html>
"""

OVERVIEW_INDEX_HTML = """\
<html><body>
<table>
  <tr><td>Used Heap</td><td>107,347,272</td></tr>
  <tr><td>Number of Objects</td><td>1,500,000</td></tr>
  <tr><td>Number of Classes</td><td>8,500</td></tr>
  <tr><td>Number of Class Loaders</td><td>25</td></tr>
  <tr><td>Number of GC Roots</td><td>6,200</td></tr>
</table>
</body></html>
"""

OVERVIEW_THREADS_HTML = """\
<html><body>
<table class="result">
  <tr><th>Object</th><th>Name</th><th>Shallow Heap</th><th>Retained Heap</th>
      <th>Max Locals</th><th>Context Class Loader</th><th>Is Daemon</th>
      <th>Priority</th><th>State</th><th>State value</th></tr>
  <tr><td>java.lang.Thread @ 0x1</td><td>main</td><td>120</td><td>53477376</td>
      <td>1024</td><td>sun.misc.Launcher$AppClassLoader</td><td>false</td>
      <td>5</td><td>RUNNABLE</td><td>0</td></tr>
  <tr><td>java.lang.Thread @ 0x2</td><td>GC-Thread</td><td>96</td><td>4096</td>
      <td>512</td><td>bootstrap</td><td>true</td>
      <td>5</td><td>WAITING</td><td>0</td></tr>
</table>
</body></html>
"""

OVERVIEW_HISTOGRAM_HTML = """\
<html><body>
<table class="result">
  <tr><th>Class Name</th><th>Objects</th><th>Shallow Heap</th><th>Retained Heap</th></tr>
  <tr><td>byte[]</td><td>250,000</td><td>26214400</td><td>26214400</td></tr>
  <tr><td>java.lang.String</td><td>180,000</td><td>7200000</td><td>33414400</td></tr>
  <tr><td>java.util.HashMap$Node</td><td>100,000</td><td>4800000</td><td>20971520</td></tr>
</table>
</body></html>
"""

TOP_COMPONENTS_INDEX_HTML = """\
<html><body>
<img alt="Pie chart overview. Total: 107,347,272" />
</body></html>
"""

TOP_COMPONENTS_CLASSLOADER_HTML = """\
<html><body>
<table>
  <tr><th>Class Loader</th><th>Objects</th><th>Retained Heap</th><th>Percent</th></tr>
  <tr><td>sun.misc.Launcher$AppClassLoader</td><td>800,000</td><td>85877818</td><td>80.00 %</td></tr>
  <tr><td>bootstrap</td><td>200,000</td><td>10734727</td><td>10.00 %</td></tr>
</table>
</body></html>
"""

TOP_COMPONENTS_CONSUMERS_HTML = """\
<html><body>
<table>
  <tr><th>Class Name</th><th>Shallow Heap</th><th>Retained Heap</th></tr>
  <tr><td>com.example.LeakyCache</td><td>48</td><td>52428800</td></tr>
  <tr><td>java.util.HashMap</td><td>1024</td><td>26214400</td></tr>
  <tr><td>java.lang.Object[]</td><td>512</td><td>10485760</td></tr>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helpers to build in-memory ZIP files
# ---------------------------------------------------------------------------

def _make_zip(files: dict[str, str]) -> Path:
    """Create a temporary ZIP from {filename: html_content} and return its path."""
    import tempfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.write(buf.getvalue())
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def suspects_zip(tmp_path: Path) -> Path:
    """Minimal Leak Suspects ZIP with index + one detail page."""
    return _make_zip({
        "index.html": SUSPECTS_INDEX_HTML,
        "1.html": SUSPECTS_DETAIL_HTML,
    })


@pytest.fixture
def overview_zip(tmp_path: Path) -> Path:
    """Minimal System Overview ZIP with index, threads, and histogram."""
    return _make_zip({
        "index.html": OVERVIEW_INDEX_HTML,
        "threads.html": OVERVIEW_THREADS_HTML,
        "class_histogram.html": OVERVIEW_HISTOGRAM_HTML,
    })


@pytest.fixture
def top_components_zip(tmp_path: Path) -> Path:
    """Minimal Top Components ZIP with index, classloaders, and consumers."""
    return _make_zip({
        "index.html": TOP_COMPONENTS_INDEX_HTML,
        "classloader_overview.html": TOP_COMPONENTS_CLASSLOADER_HTML,
        "top_consumers.html": TOP_COMPONENTS_CONSUMERS_HTML,
    })


@pytest.fixture
def reports_dir_with_zips(
    tmp_path: Path, suspects_zip: Path, overview_zip: Path, top_components_zip: Path
) -> Path:
    """Directory containing all three report ZIPs with MAT-style names."""
    import shutil
    d = tmp_path / "reports"
    d.mkdir()
    shutil.copy(suspects_zip, d / "app_Leak_Suspects.zip")
    shutil.copy(overview_zip, d / "app_System_Overview.zip")
    shutil.copy(top_components_zip, d / "app_Top_Components.zip")
    return d


@pytest.fixture
def test_client():
    """FastAPI TestClient for route-level tests."""
    from httpx import ASGITransport, AsyncClient
    from app import app
    # Use synchronous TestClient from starlette (bundled with fastapi)
    from starlette.testclient import TestClient
    return TestClient(app)
