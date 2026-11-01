"""C-8 and the rule-source note (feature 016; FR-016.28, FTDD 016 section 7.6), pure."""

from __future__ import annotations

from dataclasses import replace

from src.services.publishing.checks import (
    CarriedRuleSetFacts,
    Outcome,
    evaluate_checks,
    push_allowed,
)
from tests.unit.publishing.test_checks import by_check, green


def _set(**kw: object) -> CarriedRuleSetFacts:
    base = {"name": "rs-a", "version": 2, "behaviour_key": "key-a"}
    return CarriedRuleSetFacts(**{**base, **kw})  # type: ignore[arg-type]


def test_c8_is_green_with_no_rule_sets_and_when_not_recorded() -> None:
    assert by_check(evaluate_checks(green()))["C-8"] is Outcome.GREEN
    outcomes = evaluate_checks(replace(green(), carried_rule_sets=None))
    c8 = next(o for o in outcomes if o.check == "C-8")
    assert c8.outcome is Outcome.GREEN and "not recorded" in c8.reason


def test_c8_is_amber_naming_the_rule_set_and_the_violation() -> None:
    inp = replace(
        green(), carried_rule_sets=[_set(), _set(name="rs-b", violations=("rs-b/c1: constructed",))]
    )
    outcomes = evaluate_checks(inp)
    c8 = next(o for o in outcomes if o.check == "C-8")
    assert c8.outcome is Outcome.AMBER and "rs-b@2" in c8.reason and "rs-b/c1" in c8.reason
    assert c8.evidence["violations"] == [
        {"rule_set": "rs-b@2", "violation": "rs-b/c1: constructed"}
    ]
    assert push_allowed(outcomes, "public")[0] is False
    assert push_allowed(outcomes, "private")[0] is True


def test_a_derived_set_sharing_rows_with_held_out_is_a_note_only() -> None:
    inp = replace(green(), carried_rule_sets=[_set(derived_from="v-src", overlap_rows=12)])
    outcomes = evaluate_checks(inp)
    note = next(o for o in outcomes if o.check == "N-rule_source_overlap")
    assert note.outcome is Outcome.NOTE and note.evidence["overlap_rows"] == 12
    assert push_allowed(outcomes, "public")[0] is True
    none = evaluate_checks(
        replace(green(), carried_rule_sets=[_set(derived_from="v", overlap_rows=0)])
    )
    assert all(o.check != "N-rule_source_overlap" for o in none)
