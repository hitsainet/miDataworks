"""``rule_set@1`` (FR-016.22): registered in the LIVE registry, refuses an unresolved call and a
document whose sha256 differs from the pin, writes exactly the declared columns."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.errors import OperatorError
from src.operators.native.rules.rule_set_op import RuleSetOp
from src.operators.registry import OperatorRegistry
from src.rules import rule_set
from src.rules.cases import case_input
from src.services import step_params
from src.services.rules import step_resolution

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behaviours"


def _doc(behaviour: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / behaviour / "rule_set.json").read_text())
    return data


def _params(document: dict[str, Any], **extra: Any) -> dict[str, Any]:
    text = rule_set.canonical_bytes(document).decode()
    return {
        "rule_set_name": document["name"],
        "rule_set_version": 1,
        "rule_set_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "use": "label",
        "text_column": "text",
        "rule_set_document": text,
        **extra,
    }


def _batch(document: dict[str, Any]) -> pa.Table:
    texts, elements = [], []
    for case in document["cases"]:
        t, e = case_input(case)
        texts.append(t)
        elements.append(e)
    n = len(texts)
    return pa.table(
        {
            "_dw_row_key": [f"k{i}" for i in range(n)],
            "_dw_occurrence": [0] * n,
            "text": texts,
            "element": pa.array(elements, pa.string()),
        }
    )


def test_the_live_registry_runs_rule_set_at_version_one() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    info = reg.get("rule_set", "1")
    assert info.kind == "labeler" and info.queue == "curation"
    assert reg.current_version("rule_set") == "1"


def test_the_resolver_is_registered_for_the_operator() -> None:
    assert step_params.STEP_PARAM_RESOLVERS["rule_set"] is step_resolution.resolve_params


@pytest.mark.parametrize("behaviour", ["fixture-alpha", "fixture-beta"])
def test_run_writes_the_declared_columns_with_the_cases_values(behaviour: str) -> None:
    document = _doc(behaviour)
    params = _params(document, element_column="element", output_prefix="p_")
    result = RuleSetOp().run(_batch(document), params, None)  # type: ignore[arg-type]
    rows = result.output.to_pylist()
    declared = ["p_" + o["column"] for o in document["outputs"]]
    if document.get("vetoes"):
        declared.append("p_vetoed")
    assert [c for c in result.output.column_names if c.startswith("p_")] == declared
    for row, case in zip(rows, document["cases"], strict=True):
        for column, value in case["expect"]["outputs"].items():
            assert row["p_" + column] == value
    assert result.report == {
        "rule_set_use": {
            "name": document["name"],
            "version": 1,
            "sha256": params["rule_set_sha256"],
            "behaviour_key": document["behaviour_key"],
            "use": "label",
        }
    }
    assert RuleSetOp().columns_for(params) == (["text", "element"], declared, [])


def test_without_a_document_it_refuses_and_names_no_columns() -> None:
    document = _doc("fixture-beta")
    params = _params(document)
    del params["rule_set_document"]
    with pytest.raises(OperatorError) as refused:
        RuleSetOp().run(_batch(document), params, None)  # type: ignore[arg-type]
    assert refused.value.code == "rule_set_document_missing"
    assert RuleSetOp().columns_for(params) == (["text"], [], [])


def test_a_document_that_differs_from_the_pin_is_refused() -> None:
    document = _doc("fixture-beta")
    params = _params(document)
    params["rule_set_sha256"] = "0" * 64
    with pytest.raises(OperatorError) as refused:
        RuleSetOp().run(_batch(document), params, None)  # type: ignore[arg-type]
    assert refused.value.code == "rule_set_hash_mismatch"


def test_the_resolver_fills_the_document_only_for_the_exact_pin() -> None:
    document = _doc("fixture-beta")
    text = rule_set.canonical_bytes(document).decode()
    sha = hashlib.sha256(text.encode()).hexdigest()
    stored = {(document["name"], 1): (sha, text), (document["name"], 2): ("f" * 64, "{}")}
    lookup = step_params.mapping_lookup(stored)
    pin = {"rule_set_name": document["name"], "rule_set_version": 1, "rule_set_sha256": sha}
    assert step_resolution.resolve_params(pin, lookup)["rule_set_document"] == text
    from src.services.rules.errors import RuleSetError

    with pytest.raises(RuleSetError) as refused:
        step_resolution.resolve_params({**pin, "rule_set_version": 3}, lookup)
    assert refused.value.code == "rule_set_not_found"
    with pytest.raises(RuleSetError) as mismatch:
        step_resolution.resolve_params({**pin, "rule_set_sha256": "e" * 64}, lookup)
    assert mismatch.value.code == "rule_set_hash_mismatch"
