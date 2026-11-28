"""Rule-signal operators (feature 016): ``RULE_OPERATORS`` is THE list, imported once by
``operators/native/registrations.py`` AFTER the identifier operators, because
``OperatorRegistry.current_version`` returns the last runnable entry: the upgrade plan then offers
``regex_match@1 -> @2`` (016 FTID I10). ``rule_set@1`` (slice 2) runs a stored rule set."""

from __future__ import annotations

from .regex_v2 import RegexMatchV2
from .rule_set_op import RuleSetOp

RULE_OPERATORS: tuple[type, ...] = (RegexMatchV2, RuleSetOp)
