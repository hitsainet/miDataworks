"""The dlp-ssn-us pack's look-alike list is what its generator draws (ENH-006 D128).

The list held 20 fixed values reused across about 6,600 rows, so a probe could learn the values
instead of the difference. ``scripts/make_ssn_lookalikes.py`` draws 1,000 from seeded families; this
holds the pack to it, so a hand edit, a lost seed or a validator change that admits another value
turns red here, and the pack test's ``must_not_fire`` check still reads every value.
"""

from __future__ import annotations

import json
import re

from scripts import make_ssn_lookalikes as gen
from src.rules import public_values
from src.rules.compose import CompiledRuleSet
from src.rules.rule_set import RuleSetDocument
from src.rules.validators import resolve

RULE_SET = gen.PACKS / "dlp-ssn-us" / "rule_set.json"


def _rule_set() -> dict:
    return json.loads(RULE_SET.read_text(encoding="utf-8"))


def _listed() -> list[str]:
    (decoy,) = [d for d in _rule_set()["decoys"] if d["id"] == "lookalike"]
    return decoy["generator"]["list"]


def test_the_pack_list_is_what_the_generator_draws():
    assert _listed() == gen.draw(
        _rule_set()
    ), "run: cd backend && python -m scripts.make_ssn_lookalikes --pack ../behaviours/dlp-ssn-us"


def test_the_list_is_wide_and_unique():
    values = _listed()
    assert len(values) == gen.LIMIT == 1000
    assert len(set(values)) == len(values)
    families = {name for name, _share, _drawer in gen.FAMILIES}
    assert len(families) >= 8
    assert abs(sum(share for _n, share, _d in gen.FAMILIES) - 1.0) < 1e-9


def test_no_value_is_a_readable_identifier():
    for value in _listed():
        assert not list(public_values.readings(value)), value


def test_a_nine_digit_value_is_an_ssn_that_cannot_be_issued():
    ssn = resolve("ssn_us", 1)
    nine = [v for v in _listed() if len(re.sub(r"\D", "", v)) == 9]
    assert nine, "no nine-digit look-alike: the condition would check nothing"
    for value in nine:
        assert not ssn.check(re.sub(r"\D", "", value), "", "", {}).valid, value


def test_admissible_refuses_each_kind_of_bad_value():
    """Specificity: the filter must refuse a valid SSN in every delimiter style, a valid
    nine-digit ZIP+4-shaped value, and accept the impossible 9xx one."""
    compiled = CompiledRuleSet(RuleSetDocument.model_validate(_rule_set()))
    for bad in ("123-45-6789", "123456789", "123 45 6789", "12345-6789"):
        assert not gen.admissible(bad, compiled), bad
    # No identifier reader sees these shapes; only the nine-digit condition refuses them (a
    # mutation that dropped it survived until these were here).
    for bad in ("123/45/6789", "(123) 45-6789", "123_45_6789", "123,45,6789"):
        assert not list(public_values.readings(bad)), bad
        assert not gen.admissible(bad, compiled), bad
    assert gen.admissible("94105-1804", compiled)


def test_admissible_refuses_what_the_pack_rule_fires_on():
    """The ``ssn_hit`` condition is implied by the readers today (the rule's SSN signal is the same
    validator), so no real value tells them apart; a rule set that fires on everything pins that
    the condition is still wired, for the day the rule set or the readers change."""

    class FiresOnEverything:
        def evaluate(self, text, element):
            return type("Result", (), {"outputs": {"ssn_hit": True}})()

    assert gen.admissible(
        "94105-1804", CompiledRuleSet(RuleSetDocument.model_validate(_rule_set()))
    )
    assert not gen.admissible("94105-1804", FiresOnEverything())
