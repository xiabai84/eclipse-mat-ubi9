#!/usr/bin/env python3
"""
MAT Leak Suspects Report Analyzer — standalone CLI wrapper.

This script is a thin shim kept for backward compatibility.
The implementation lives in service/analyzers/suspects.py.

Usage:
    python3 mat_suspect_analyzer.py <Leak_Suspects.zip> [-o OUTPUT_DIR] [--json]
"""

import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

from analyzers.suspects import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
