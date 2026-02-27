#!/usr/bin/env python3
"""
MAT System Overview Analyzer — standalone CLI wrapper.

This script is a thin shim kept for backward compatibility.
The implementation lives in service/analyzers/overview.py.

Usage:
    python3 mat_system_overview_analyzer.py <System_Overview.zip> [-o OUTPUT_DIR] [--json]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analyzers.overview import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
