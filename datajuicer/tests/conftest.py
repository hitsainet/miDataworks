"""Data-Juicer image tests: run in the Data-Juicer environment (CI job ``datajuicer-tests``)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dj_paths  # noqa: E402,F401
