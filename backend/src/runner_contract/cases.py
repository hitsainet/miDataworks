"""Load and validate ``runner-cases-v1.json`` (FR-018.10 – FR-018.13; FTDD §4.5).

The cases file is HAND-WRITTEN (formatted by ``backend/scripts/format_runner_cases.py``) and packaged
at ``src/schemas/data/`` because the image has no ``docs/``. It carries the documents the cases
reference, the conformance cases, the hostile cases (by class number), the hash vectors, one import
vector per allowed module, and one edge pair per floor limit. ``load_cases`` validates it against its
own ``case_schema`` before returning it.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import jsonschema

CASES_FILE_NAME = "runner-cases-v1.json"
PACKAGED_CASES_PATH = Path(__file__).resolve().parents[1] / "schemas" / "data" / CASES_FILE_NAME


def packaged_cases_path() -> Path:
    return PACKAGED_CASES_PATH


@functools.lru_cache(maxsize=1)
def load_cases() -> dict[str, Any]:
    """The packaged cases file, validated against its own ``case_schema`` (read once per process)."""
    return load_cases_from(PACKAGED_CASES_PATH)


def load_cases_from(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(data["case_schema"])
    jsonschema.Draft202012Validator(data["case_schema"]).validate(data)
    return data
