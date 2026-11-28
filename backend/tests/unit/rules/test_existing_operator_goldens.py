"""The operators that existed before 016 keep byte-identical output (FR-016.1, .3, .25; FTASKS 0.2).

``expected.json`` was recorded on ``main`` before slice 1 changed anything (``check_one``'s scan,
``manifest()``'s new ``version`` keyword, ``regex_match@2`` beside ``@1``). Every recipe is rebuilt
through the real executor with its pinned versions; every row, every column and every row hash
must equal the recording, and every pre-016 native operator keeps its manifest hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.core.canonical_json import canonical_sha256
from tests.support.rule_goldens import EXPECTED_PATH, RECIPES_PATH, manifest_hashes, run_recipe

SPEC: dict[str, Any] = json.loads(RECIPES_PATH.read_text(encoding="utf-8"))
EXPECTED: dict[str, Any] = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def test_the_recording_covers_every_recipe_and_operator() -> None:
    """Not vacuous: every recipe has a recording and every pre-016 operator is exercised."""
    assert set(EXPECTED["recipes"]) == {r["id"] for r in SPEC["recipes"]}
    used = {(name, version) for r in SPEC["recipes"] for name, version, _ in r["steps"]}
    assert used == {
        ("regex_match", "1"),
        ("phrase_proximity", "1"),
        ("checksum_validate", "1"),
        ("rule_label", "1"),
        ("synth_identifier", "1"),
    }
    modes = {
        p.get("mode", "value")
        for r in SPEC["recipes"]
        for name, _, p in r["steps"]
        if name == "checksum_validate"
    }
    assert modes == {"value", "scan"}
    assert all(EXPECTED["recipes"][k]["rows"] for k in EXPECTED["recipes"])


@pytest.mark.parametrize("recipe", SPEC["recipes"], ids=lambda r: r["id"])
def test_the_rebuild_equals_the_recording(recipe: dict[str, Any], data_dir: Path) -> None:
    rows = run_recipe(recipe, SPEC["inputs"])
    golden = EXPECTED["recipes"][recipe["id"]]
    assert [canonical_sha256(r) for r in rows] == golden["row_hashes"]
    assert rows == golden["rows"]


def test_every_pre_016_operator_keeps_its_manifest_hash() -> None:
    live = manifest_hashes()
    recorded: dict[str, str] = EXPECTED["manifest_hashes"]
    assert recorded, "no manifest hashes recorded"
    changed = {ref: (h, live.get(ref)) for ref, h in recorded.items() if live.get(ref) != h}
    assert not changed, f"manifest hashes changed (ref: (recorded, live)): {changed}"
