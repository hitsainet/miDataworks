"""``docs/mcp-annotations.json`` is what the live registry and the tools' code generate (022 FR-022.26).

miWorkbench 045's relay test reads this file (version 2) to give each ``dataworks_*`` relay the
source tool's hints when it makes the same calls (suite A13 as amended by A30), so a stale or
hand-edited file would hand a relay the wrong hints.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.generate_mcp_call_map import ANNOTATIONS_PATH, annotations_document

REGENERATE = "cd backend && python -m scripts.generate_mcp_call_map --write"


def published() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    return document


def test_the_committed_table_is_the_generated_one() -> None:
    assert ANNOTATIONS_PATH.exists(), f"docs/mcp-annotations.json is missing: {REGENERATE}"
    assert (
        ANNOTATIONS_PATH.read_text(encoding="utf-8") == annotations_document()
    ), f"docs/mcp-annotations.json is stale or was edited by hand: {REGENERATE}"


def test_the_shape_is_version_2() -> None:
    document = published()
    assert document["server"] == "midataworks"
    assert document["version"] == 2
    assert len(document["tools"]) >= 220
    for name, entry in document["tools"].items():
        assert set(entry) == {"title", "readOnlyHint", "destructiveHint", "calls"}, name
        assert isinstance(entry["readOnlyHint"], bool), name
        assert isinstance(entry["destructiveHint"], bool), name
        for call in entry["calls"]:
            assert set(call) == {"method", "path"}, name
            assert call["path"].startswith("/api/"), name
        assert entry["calls"] == sorted(entry["calls"], key=lambda c: (c["path"], c["method"]))


def test_only_the_howto_calls_nothing() -> None:
    silent = sorted(n for n, e in published()["tools"].items() if not e["calls"])
    assert silent == ["dataworks_howto"], silent
