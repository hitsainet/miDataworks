"""Paths shared by the Data-Juicer scripts and tests (this directory is not a package)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
BACKEND = REPO / "backend"
FIXTURE = BACKEND / "tests" / "fixtures" / "operators" / "contract_fixture.parquet"
EXPECTATIONS = BACKEND / "tests" / "fixtures" / "operators" / "expectations"
CASES = ROOT / "contract_cases.json"

# The runner is imported from the backend tree exactly as the image copies it (src/operators/
# datajuicer/runner.py); it imports nothing else from src.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
