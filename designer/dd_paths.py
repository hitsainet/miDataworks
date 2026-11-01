"""Paths shared by the Data Designer scripts and tests (this directory is not a package)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
BACKEND = REPO / "backend"
CATALOGUE = BACKEND / "src" / "operators" / "data_designer" / "catalogue.json"

# The runner and relay are imported from the backend tree exactly as the image copies them.
for path in (BACKEND, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
