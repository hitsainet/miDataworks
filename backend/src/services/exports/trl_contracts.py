"""TRL's dataset column contracts at the pinned versions (FR-008.25; FTDD 008 section 3).

Read from the wheels on 2026-10-07 (``trl-1.14.1-py3-none-any.whl``, ``trl-0.29.1-…whl``,
downloaded from PyPI). Each entry names the file and line the contract was read from, so a re-pin
re-reads the same place. TRL 1.14.1 has no PRM trainer; PRM is pinned to 0.29.1, the last release
shipping ``trl/experimental/prm/prm_trainer.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

TRL_VERSION = "1.14.1"
PRM_TRL_VERSION = "0.29.1"


@dataclass(frozen=True)
class Variant:
    columns: tuple[str, ...]
    #: Columns whose values must be booleans (KTO ``label``, PRM ``labels``).
    boolean: tuple[str, ...] = ()
    boolean_list: tuple[str, ...] = ()


@dataclass(frozen=True)
class TrlContract:
    trl_type: str
    trl_version: str
    #: Alternatives in preference order; the first the version can form is used.
    variants: tuple[Variant, ...]
    source: str
    #: GRPO passes every other dataset column to the reward function as a keyword.
    extra_columns_allowed: bool = False


CONTRACTS: dict[str, TrlContract] = {
    "sft": TrlContract(
        "sft",
        TRL_VERSION,
        (Variant(("messages",)), Variant(("text",)), Variant(("prompt", "completion"))),
        "trl/trainer/sft_trainer.py:567-569 (1.14.1)",
    ),
    "dpo": TrlContract(
        "dpo",
        TRL_VERSION,
        (Variant(("prompt", "chosen", "rejected")),),
        "trl/trainer/dpo_trainer.py:221 (1.14.1)",
    ),
    "kto": TrlContract(
        "kto",
        TRL_VERSION,
        (Variant(("prompt", "completion", "label"), boolean=("label",)),),
        "trl/trainer/kto_trainer.py:228-229 (1.14.1)",
    ),
    "grpo_prompt": TrlContract(
        "grpo_prompt",
        TRL_VERSION,
        (Variant(("prompt",)),),
        "trl/trainer/grpo_trainer.py:210, 1670-1730 (1.14.1)",
        extra_columns_allowed=True,
    ),
    "prm": TrlContract(
        "prm",
        PRM_TRL_VERSION,
        (Variant(("prompt", "completions", "labels"), boolean_list=("labels",)),),
        "trl/experimental/prm/prm_trainer.py:270, 297-298 (0.29.1)",
    ),
}


def formable_variant(trl_type: str, columns: set[str]) -> tuple[Variant | None, list[str]]:
    """The first variant whose columns the version has, else (None, the first variant's missing)."""
    contract = CONTRACTS[trl_type]
    for variant in contract.variants:
        if all(c in columns for c in variant.columns):
            return variant, []
    first = contract.variants[0]
    return None, [c for c in first.columns if c not in columns]
