"""``rule_set@1`` refuses code-rule documents it cannot trust (feature 018 slice 4; FTDD §7.6).

Driven through the operator's real ``run`` (no build): a document naming a code rule without the
resolved documents is ``code_rule_documents_missing`` (an unresolved call path), and a handed-over
document whose bytes do not hash to the signal's ``code_sha256`` is ``code_rule_hash_mismatch`` - the
runner is never asked.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pyarrow as pa
import pytest

from src.core.canonical_json import canonical_json
from src.operators.errors import StepFailed
from src.operators.native.rules.rule_set_op import RuleSetOp
from src.runner_contract.hashing import code_sha256
from tests.support import code_rule_fixtures as fx


class NoRunner:
    calls = 0

    def run(self, *a: Any, **k: Any) -> Any:
        NoRunner.calls += 1
        raise AssertionError("the runner must not be asked")


class Ctx:
    runner = NoRunner()


def _params(document_text: str, resolution: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "rule_set_name": "gamma-sum-set",
        "rule_set_version": 1,
        "rule_set_sha256": hashlib.sha256(document_text.encode()).hexdigest(),
        "use": "decoy_check",
        "text_column": "text",
        "rule_set_document": document_text,
    }
    if resolution is not None:
        params["code_rule_resolution"] = resolution
    return params


def _batch() -> pa.Table:
    return pa.table({"_dw_row_key": ["k0"], "_dw_occurrence": [0], "text": ["tally 28"]})


def _document() -> tuple[str, dict[str, Any]]:
    rule = fx.fixture("fixture-gamma")["document"]
    sha = code_sha256(rule)
    rs = canonical_json(fx.rule_set_document("fixture-gamma", sha)).decode()
    return rs, rule


def test_a_code_rule_set_without_its_resolved_documents_is_refused() -> None:
    rs, _ = _document()
    with pytest.raises(StepFailed) as caught:
        RuleSetOp().run(_batch(), _params(rs, None), Ctx())  # type: ignore[arg-type]
    assert caught.value.code == "code_rule_documents_missing"


def test_a_document_that_does_not_hash_to_the_signal_is_refused_and_never_run() -> None:
    rs, rule = _document()
    sha = code_sha256(rule)
    tampered = dict(rule, source=rule["source"] + "# changed\n")
    resolution = json.dumps(
        {
            "documents": {sha: canonical_json(tampered).decode()},
            "versions": {sha: "crv_x"},
            "approvals": {sha: 1},
            "runner_limits": {},
        }
    )
    with pytest.raises(StepFailed) as caught:
        RuleSetOp().run(_batch(), _params(rs, resolution), Ctx())  # type: ignore[arg-type]
    assert caught.value.code == "code_rule_hash_mismatch"
    assert NoRunner.calls == 0
