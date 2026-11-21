"""Code rules inside ``compose`` and the failure share, held at the line (018 Review R3).

- FR-018.38 for OUTPUT columns: a ``first`` or ``spans`` output over an expression that names a code
  rule is null - a code rule reports a count, never a position - even when a pattern beside it in
  the same expression placed a span (the earlier tests held only the per-signal and per-expression
  ``spans: null``).
- I8's threshold: a step fails only ABOVE ``CODE_RULE_FAILURE_SHARE``; exactly at it, it completes.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from src.core.config import get_settings
from src.core.errors import AppError
from src.rules.compose import CompiledRuleSet
from src.rules.rule_set import RuleSetDocument
from src.services.code_rules import uses
from tests.support import code_rule_fixtures as fx


def _compiled(behaviour: str) -> CompiledRuleSet:
    document: dict[str, Any] = copy.deepcopy(fx.rule_set_document(behaviour, "a" * 64))
    document["outputs"] += [
        {"column": "first_hit", "type": "string", "value": {"first": "hit"}},
        {"column": "spans_hit", "type": "string", "value": {"spans": "hit"}},
        {"column": "first_both", "type": "string", "value": {"first": "both"}},
        {"column": "spans_both", "type": "string", "value": {"spans": "both"}},
        {"column": "first_word", "type": "string", "value": {"first": "worded"}},
    ]
    word = fx.RULE_SET_SHAPES[behaviour]["word"]
    for case in document["cases"]:  # every case states every column (the schema's rule)
        worded = case["expect"]["outputs"]["worded"]
        case["expect"]["outputs"].update(
            first_hit=None,
            spans_hit=None,
            first_both=None,
            spans_both=None,
            first_word=word if worded else None,
        )
    return CompiledRuleSet(RuleSetDocument.model_validate(document), code_rules=True)


@pytest.mark.parametrize("behaviour", ["fixture-gamma", "fixture-delta"])
def test_a_position_output_over_a_code_rule_expression_is_null(behaviour: str) -> None:
    compiled = _compiled(behaviour)
    text = fx.RULE_SET_SHAPES[behaviour]["texts"][0]  # the word and the code rule both fire
    result = compiled.evaluate(text, None, {"coded": 2})
    assert result.outputs["both"] is True and result.outputs["n"] == 2
    for column in ("first_hit", "spans_hit", "first_both", "spans_both"):
        assert result.outputs[column] is None, (column, result.outputs[column])
    # a position output over a pattern-only expression still reads its span
    assert result.outputs["first_word"] == fx.RULE_SET_SHAPES[behaviour]["word"]


def test_the_failure_share_fails_only_above_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "code_rule_failure_share", 0.01)
    payload = {
        "code_sha256": "a" * 64,
        "name": "gamma-digit-sum",
        "version": 1,
        "reasons": {"rule_error": 2},
    }
    at = uses.Counter({"rows": 100, "operator_code_failed": 1})
    uses._failure_share([payload], at, 100)  # exactly 1%: completes
    above = uses.Counter({"rows": 100, "operator_code_failed": 2})
    with pytest.raises(AppError) as caught:
        uses._failure_share([payload], above, 100)
    assert caught.value.code == "code_rule_failure_share_exceeded"
    assert caught.value.details["leading_reason"] == "rule_error"
