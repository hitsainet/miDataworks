"""Every offered Data-Juicer operator on the contract fixture matches its recorded expectations
(FR-003.14; FTASKS 7.7). An engine upgrade must pass this before the image ships."""

from __future__ import annotations

import json

import pytest

import dj_paths
from contract import outcome

CASES = json.loads(dj_paths.CASES.read_text())
CATALOGUE = json.loads(
    (dj_paths.BACKEND / "src" / "operators" / "datajuicer" / "catalogue.json").read_text()
)


def test_every_offered_operator_has_a_case() -> None:
    offered = {o["op_name"] for o in CATALOGUE["operators"]}
    assert offered == set(CASES)


@pytest.mark.parametrize("op_name", sorted(CASES))
def test_operator_matches_its_expectations(op_name: str) -> None:
    expected = json.loads((dj_paths.EXPECTATIONS / f"{op_name}.json").read_text())
    actual = outcome(op_name, CASES[op_name])
    assert actual == expected


@pytest.mark.parametrize("op_name", sorted(CASES))
def test_the_fixture_makes_each_operator_keep_and_drop(op_name: str) -> None:
    """A fixture where an operator keeps everything (or nothing) proves nothing about it."""
    expected = json.loads((dj_paths.EXPECTATIONS / f"{op_name}.json").read_text())
    assert expected["kept"] and expected["dropped"]
    entry = next(o for o in CATALOGUE["operators"] if o["op_name"] == op_name)
    if entry["stats_key"]:
        assert expected["distinct_stats"] > 1, "the statistic must differ across rows"
        assert all(d["stats"][entry["stats_key"]] is not None for d in expected["dropped"])
    else:  # the deduplicator names the row kept in place of each drop
        assert all(d["kept_key"] for d in expected["dropped"])
