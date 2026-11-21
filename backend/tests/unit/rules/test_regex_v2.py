"""``regex_match@2`` beside ``@1`` (FR-016.12, FR-016.25; FTASKS 4.1, 4.2; FTID 016 I10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.operators.errors import StepFailed
from src.operators.registry import OperatorRegistry
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator


def test_the_live_registry_offers_both_versions_and_two_is_current() -> None:
    production = OperatorRegistry.build(catalogues=(), entry_points=())
    states = {e.ref: s for e, s in production.entries()}
    assert states.get("regex_match@1") == "allowed"
    assert states.get("regex_match@2") == "allowed"
    assert production.current_version("regex_match") == "2"
    manifest = production.entry("regex_match", "2").manifest
    assert manifest is not None and "fold_fullwidth_ascii" in manifest.params_schema["required"]


def _rows(texts: list[str]) -> tuple[object, dict[str, str]]:
    return with_system_columns([{"text": t} for t in texts]), {"text": "content"}


def test_v2_reads_ascii_classes_and_folds_only_when_asked(data_dir: Path) -> None:
    table, roles = _rows(["５５５-１２-３４５６", "é 123-45-6789\n"])
    pattern = r"\d{3}-\d{2}-\d{4}$"
    plain = run_operator(
        "regex_match",
        {"column": "text", "pattern": pattern, "fold_fullwidth_ascii": False},
        table,
        roles,
        version="2",
    )
    folded = run_operator(
        "regex_match",
        {"column": "text", "pattern": pattern, "fold_fullwidth_ascii": True},
        table,
        roles,
        version="2",
    )
    v1 = run_operator(
        "regex_match", {"column": "text", "pattern": pattern}, table, roles, version="1"
    )
    p, f, o = (r.output.to_pylist() for r in (plain, folded, v1))
    assert [r["rx_matched"] for r in p] == [False, False]  # ASCII \d; $ not before a final \n
    assert [r["rx_matched"] for r in f] == [True, False]
    assert f[0]["rx_first"] == "５５５-１２-３４５６"
    assert [r["rx_matched"] for r in o] == [True, True]  # @1 unchanged: Python re
    assert json.loads(f[0]["rx_spans"]) == [[0, 11]]


def test_v2_refuses_a_pattern_naming_rule_and_position(data_dir: Path) -> None:
    table, roles = _rows(["x"])
    with pytest.raises(StepFailed) as caught:
        run_operator(
            "regex_match",
            {"column": "text", "pattern": r"ab(?=c)", "fold_fullwidth_ascii": False},
            table,
            roles,
            version="2",
        )
    assert caught.value.details["rule"] == "lookaround" and caught.value.details["position"] == 2
