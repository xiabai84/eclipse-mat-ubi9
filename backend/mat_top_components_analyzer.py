#!/usr/bin/env python3
"""
MAT Top Components Analyzer — standalone CLI wrapper.

This script is a thin shim kept for backward compatibility.
The implementation lives in service/analyzers/top_components.py.

Usage:
    python3 mat_top_components_analyzer.py <Top_Components.zip> [-o OUTPUT_DIR] [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analyzers.top_components import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
