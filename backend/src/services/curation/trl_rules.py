"""The TRL format validator's rules (FR-004.22, 004.23; FTID 004 §7.7; T-15).

Pure. Each rule returns ``ok``, ``fail(message)`` or ``not_checked(reason)``; a rule that could not be
evaluated is NEVER reported as passed. The column contracts are TRL's at the version feature 008
pins (008 ``services/exports/trl_contracts.py``: TRL 1.14.1; PRM 0.29.1). When 008's module is
present the pin is IMPORTED from it, so there is one authority; ``test_trl_rules.py`` asserts the
two are the same object whenever 008 is installed. On a tree without 008 the same values are
declared here.

Rules: required columns present for SFT, DPO, KTO, GRPO prompt-only and PRM (the first formable
variant); chat cells' roles valid and alternating after an optional system turn, no empty turn;
DPO ``chosen`` and ``rejected`` non-empty; KTO ``label`` boolean; PRM ``completions`` and ``labels``
the same length with boolean labels; context window always ``not_checked`` (no tokenizer, T-15).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def _pins() -> tuple[str, str]:
    """008's pins when 008 is installed (one authority), else the same declared values."""
    try:
        module = importlib.import_module("src.services.exports.trl_contracts")
    except ModuleNotFoundError as exc:
        if exc.name not in ("src.services.exports", "src.services.exports.trl_contracts"):
            raise
        return "1.14.1", "0.29.1"
    return str(module.TRL_VERSION), str(module.PRM_TRL_VERSION)


TRL_VERSION, PRM_TRL_VERSION = _pins()

#: Required columns per target type, alternatives in preference order (008's CONTRACTS).
VARIANTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "sft": (("messages",), ("text",), ("prompt", "completion")),
    "dpo": (("prompt", "chosen", "rejected"),),
    "kto": (("prompt", "completion", "label"),),
    "grpo_prompt": (("prompt",),),
    "prm": (("prompt", "completions", "labels"),),
}
TARGET_TYPES: tuple[str, ...] = tuple(VARIANTS)
CHAT_ROLES = frozenset({"system", "user", "assistant"})
NO_TOKENIZER = "no tokenizer in v1 (T-15): the context-window rule cannot be evaluated"


@dataclass(frozen=True)
class Outcome:
    status: str  # "ok" | "fail" | "not_checked"
    message: str = ""


OK = Outcome("ok")


def fail(message: str) -> Outcome:
    return Outcome("fail", message)


def not_checked(reason: str) -> Outcome:
    return Outcome("not_checked", reason)


def formable(target: str, columns: Sequence[str]) -> tuple[tuple[str, ...] | None, list[str]]:
    """The first variant the columns can form, else (None, the first variant's missing columns)."""
    for variant in VARIANTS[target]:
        if all(c in columns for c in variant):
            return variant, []
    first = VARIANTS[target][0]
    return None, [c for c in first if c not in columns]


def chat_ok(value: Any) -> Outcome:
    """Roles valid, alternating user/assistant after an optional system turn, no empty turn."""
    if not isinstance(value, list):
        return OK  # a plain-text cell is not a chat: this rule does not apply
    if not value:
        return fail("the conversation has no turns")
    expected = "user"
    for index, message in enumerate(value):
        role = message.get("role") if isinstance(message, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if role not in CHAT_ROLES:
            return fail(f"turn {index} has role {role!r}")
        if not isinstance(content, str) or not content.strip():
            return fail(f"turn {index} ({role}) is empty")
        if role == "system":
            if index != 0:
                return fail(f"turn {index} is a system turn after the first")
            continue
        if role != expected:
            return fail(f"turn {index} is {role} where {expected} was expected (roles alternate)")
        expected = "assistant" if role == "user" else "user"
    return OK


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value)
    return True


@dataclass(frozen=True)
class Rule:
    id: str
    columns: tuple[str, ...]
    check: Callable[[Mapping[str, Any]], Outcome]


def _chat_rule(column: str) -> Rule:
    return Rule(f"chat_turns:{column}", (column,), lambda row: chat_ok(row.get(column)))


def _nonempty_rule(column: str) -> Rule:
    return Rule(
        f"non_empty:{column}",
        (column,),
        lambda row: OK if _non_empty(row.get(column)) else fail(f"{column} is empty"),
    )


def _bool_rule(column: str) -> Rule:
    def check(row: Mapping[str, Any]) -> Outcome:
        return OK if isinstance(row.get(column), bool) else fail(f"{column} is not a boolean")

    return Rule(f"boolean:{column}", (column,), check)


def _prm_rule(row: Mapping[str, Any]) -> Outcome:
    completions, labels = row.get("completions"), row.get("labels")
    if not isinstance(completions, list) or not isinstance(labels, list):
        return fail("completions and labels must both be lists")
    if len(completions) != len(labels):
        return fail(f"{len(completions)} completions but {len(labels)} labels")
    if not all(isinstance(x, bool) for x in labels):
        return fail("labels must be booleans")
    return OK


CONTEXT_WINDOW = Rule("context_window", (), lambda row: not_checked(NO_TOKENIZER))


def rules_for(target: str, variant: Sequence[str]) -> list[Rule]:
    out: list[Rule] = [_chat_rule(c) for c in variant]
    if target == "dpo":
        out += [_nonempty_rule("chosen"), _nonempty_rule("rejected"), _nonempty_rule("prompt")]
    elif target == "kto":
        out += [_bool_rule("label"), _nonempty_rule("completion")]
    elif target == "prm":
        out.append(Rule("prm_lengths", ("completions", "labels"), _prm_rule))
    else:
        out += [_nonempty_rule(c) for c in variant]
    out.append(CONTEXT_WINDOW)
    return out


def check_row(rules: Sequence[Rule], row: Mapping[str, Any]) -> list[tuple[Rule, Outcome]]:
    """Every rule's outcome for one row (failures and not-checked both reported)."""
    return [(rule, rule.check(row)) for rule in rules]
