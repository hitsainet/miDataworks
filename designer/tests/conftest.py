"""Data Designer image tests: run in the Data Designer environment (CI job ``designer-tests``)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dd_paths  # noqa: E402,F401
