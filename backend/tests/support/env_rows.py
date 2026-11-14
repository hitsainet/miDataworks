"""Put the sibling rename rows back in transition for one test (feature 020; FTASKS 19.3).

Since P7 every ``core/env_names.RENAMES`` row is retired, so an old name is never read and the
old-name evidence machinery (touch points, the store, the health block, ``env_legacy``) never sees
one. That machinery stays until P8 drops its table, and its tests still drive it: they put the rows
back in transition here, through the module attributes production reads (``RENAMES`` and the
``_BY_FIELD`` index built from it), and ``monkeypatch`` restores both after the test.
"""

from __future__ import annotations

import pytest

from src.core import env_names


def in_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = tuple(env_names.EnvRename(r.field, r.new, r.old) for r in env_names.RENAMES)
    monkeypatch.setattr(env_names, "RENAMES", rows)
    monkeypatch.setattr(env_names, "_BY_FIELD", {row.field: row for row in rows})
