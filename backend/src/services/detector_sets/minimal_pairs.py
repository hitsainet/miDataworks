"""The minimal-pair rules (009 FR-009.60 - FR-009.64; operator decision 2026-10-07).

PURE: the standard library only. The two operators (``operators/native/detector/minimal_pairs.py``)
and the chain service (``minimal_pair_chain.py``) call these; nothing re-implements them.

- **Edit size** (FR-009.63): characters and words changed between the seed and its counterpart,
  from ``difflib``'s opcodes with ``autojunk`` off (so a long text is not judged by a heuristic):
  each non-equal span counts the larger of its two sides. Zero means the generator returned the
  seed unchanged, which is no flip at all.
- **A verified flip** (FR-009.61): the judge gave the counterpart ``flip_to`` AND the seed
  ``flip_from``, neither provisional. Anything else drops the pair with the judge's verdict as its
  reason: a seed that already had the target class was never flipped, however the counterpart
  reads.
- **Pair ID** (FR-009.60; FTID 7.7): the seed's row key, on both rows.
- **Steering state** (FR-009.63): what miLLM reported for the counterpart's request, verbatim;
  ``not reported`` when it reported nothing, ``not applicable`` when the endpoint has no steering
  contract (007's own words; never "unsteered").
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

NOT_REPORTED = "not reported"
NOT_APPLICABLE = "not applicable"
#: Judge outcomes that carry no verdict.
NO_VERDICT = ("parse_failure", "skipped")


@dataclass(frozen=True)
class EditSize:
    chars: int
    words: int

    def as_dict(self) -> dict[str, int]:
        return {"chars": self.chars, "words": self.words}


def _changed(before: Sequence[Any], after: Sequence[Any]) -> int:
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    return sum(
        max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"
    )


def edit_size(before: str, after: str) -> EditSize:
    """Characters and whitespace-separated words changed from ``before`` to ``after``."""
    return EditSize(_changed(before, after), _changed(before.split(), after.split()))


def over_cap(size: EditSize, max_chars: int | None, max_words: int | None) -> str | None:
    """``"chars"`` or ``"words"`` when the edit is larger than its cap; None when within both."""
    if max_chars is not None and size.chars > int(max_chars):
        return "chars"
    if max_words is not None and size.words > int(max_words):
        return "words"
    return None


@dataclass(frozen=True)
class JudgeVerdict:
    outcome: str | None
    provisional: bool = False


@dataclass(frozen=True)
class FlipCheck:
    verified: bool
    reason_code: str
    reason: str


def verify_flip(
    seed: JudgeVerdict | None,
    counterpart: JudgeVerdict | None,
    flip_from: str,
    flip_to: str,
) -> FlipCheck:
    """Whether the judge verified the flip (FR-009.61). A missing, unparsed or provisional verdict
    on either row is not a verification."""
    for name, verdict in (("counterpart", counterpart), ("seed", seed)):
        if verdict is None or verdict.outcome is None:
            return FlipCheck(False, "judge_missing", f"the judge gave the {name} no verdict")
        if verdict.outcome in NO_VERDICT:
            return FlipCheck(
                False, "judge_missing", f"the judge's answer for the {name} was {verdict.outcome}"
            )
        if verdict.provisional:
            return FlipCheck(
                False, "judge_provisional", f"the judge's verdict on the {name} is provisional"
            )
    assert seed is not None and counterpart is not None
    if counterpart.outcome != flip_to:
        return FlipCheck(
            False,
            "flip_not_verified",
            f"the judge read the counterpart as {counterpart.outcome!r}, not {flip_to!r}",
        )
    if seed.outcome != flip_from:
        return FlipCheck(
            False,
            "seed_not_flip_from",
            f"the judge read the seed as {seed.outcome!r}, not {flip_from!r}: it was never "
            f"{flip_from!r}, so nothing was flipped",
        )
    return FlipCheck(True, "verified", f"{flip_from!r} -> {flip_to!r}")


def pair_id(seed_row_key: str) -> str:
    """The pair's ID: the seed's row key (FTID 009 section 7.7)."""
    return str(seed_row_key)


def steering_state(reported: str | None, steering_check: str | None) -> str:
    """The counterpart request's steering state as recorded by 007 (FR-009.63)."""
    if steering_check == "not_applicable" and not reported:
        return NOT_APPLICABLE
    return reported if reported else NOT_REPORTED
