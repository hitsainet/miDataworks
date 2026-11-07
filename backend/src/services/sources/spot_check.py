"""miStudio 038's spot-check disagreement export, as miDataworks recognises it (015 FR-015.10,
FR-015.11; ENH-005 D33, D57).

The column names are OWNED by miStudio: 038 publishes ``docs/schemas/spot-check-disagreement-v1.json``
(``mistudio.spot-check-disagreement/v1``, generated from its ``DisagreementRow``). miDataworks vendors
that file byte-identical into ``src/schemas/vendored/`` (pinned by
``tests/unit/contract/test_spot_check_schema_vendored.py``) and derives the pinned columns from it.

Until the file is vendored, ``SPOT_CHECK_COLUMNS`` is EMPTY and recognition is off: a guessed column
list would claim a contract miStudio has not published. Detection says so in its reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_FILE_NAME = "spot-check-disagreement-v1.json"
VENDORED_PATH = Path(__file__).resolve().parents[2] / "schemas" / "vendored" / SCHEMA_FILE_NAME
PROVENANCE = "mistudio_spot_check/v1"
#: The chat column every disagreement row carries (role/content form).
MESSAGES = "messages"
#: The provenance column read as the session (``group`` in the manifest, FR-015.14).
SESSION_COLUMN = "session_id"


def pinned_columns(document: dict[str, Any] | None) -> tuple[str, ...]:
    """The schema's required columns other than ``messages``, sorted (empty without a schema)."""
    if not document:
        return ()
    return tuple(sorted(set(document.get("required") or ()) - {MESSAGES}))


def _load() -> dict[str, Any] | None:
    if not VENDORED_PATH.exists():
        return None
    loaded: dict[str, Any] = json.loads(VENDORED_PATH.read_bytes())
    return loaded


SPOT_CHECK_COLUMNS: tuple[str, ...] = pinned_columns(_load())
