"""Labeling decisions as pure functions (FTID 005 sections 1 and 7.1; FTDD 005 section 6.6).

Every rule a mutation could break lives here, with no I/O, and is CALLED from exactly one site that
``tests/unit/labeling/test_call_sites.py`` pins by walking the AST:

- :func:`decide_binary` — the two-threshold rule, inclusive at both thresholds (FR-005.18,
  prototype ``scripts/build_labels.py::judge_label``). Uncertain rows are EXCLUDED, never guessed.
- :func:`decide_top_label` — more than two labels (T-18).
- :func:`approval_needed` — P-07: an agent's rows on one version within the window, over the
  threshold (strictly ``>``: "over 5,000", D6b).
- :func:`labeler_identity`, :func:`identity_hash`, :func:`fingerprint`, :func:`reuse_allowed` —
  FR-005.28, FR-005.29; thresholds are not part of the fingerprint.
- :func:`swap_and_agree`, :func:`parse_failure_exceeded`, :func:`aggregate_verdict` — judge
  controls (FR-005.41, FR-005.43, FR-005.45).
- :func:`wilson_interval` — the keep-share interval (FR-005.19).

One import beyond the standard library: the project's one canonical-JSON function (ADR-005), so a
fingerprint hashed here is byte-for-byte the one hashed anywhere else.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from ..core.canonical_json import canonical_sha256

#: A fact the endpoint did not report (FR-005.25). Never replaced by a default.
NOT_REPORTED = "not reported"
#: Classifier scoring on miLLM without an ``X-miLLM-Steering`` header (FR-005.25, X-09).
UNSTEERED_SCORING = "unsteered (scoring mode)"
#: A judge seed the server did not echo (FR-005.42).
SEED_NOT_CONFIRMED = "seed not confirmed"

BinaryOutcome = Literal["positive", "negative", "excluded"]


class ThresholdsInvalid(ValueError):
    """Thresholds missing, outside [0, 1], or not ordered (T-19). ``field`` names the culprit."""

    def __init__(self, message: str, field: str) -> None:
        super().__init__(message)
        self.field = field


# --- thresholds -------------------------------------------------------------------------------


def decide_binary(p: float, positive_at: float, negative_at: float) -> BinaryOutcome:
    """Positive when ``p >= positive_at``; negative when ``p <= negative_at``; else excluded."""
    if p >= positive_at:
        return "positive"
    if p <= negative_at:
        return "negative"
    return "excluded"


def validate_thresholds(positive_at: float | None, negative_at: float | None) -> None:
    """Both required (nothing pre-filled, T-19), each in [0, 1], negative strictly lower."""
    if positive_at is None:
        raise ThresholdsInvalid(
            "Set 'positive at or above'; both thresholds are required.", "threshold_positive"
        )
    if negative_at is None:
        raise ThresholdsInvalid(
            "Set 'negative at or below'; both thresholds are required.", "threshold_negative"
        )
    for name, value in (("threshold_positive", positive_at), ("threshold_negative", negative_at)):
        if not (isinstance(value, int | float) and not isinstance(value, bool)):
            raise ThresholdsInvalid(f"{name} must be a number.", name)
        if math.isnan(value) or value < 0.0 or value > 1.0:
            raise ThresholdsInvalid(f"{name} must be between 0 and 1.", name)
    if not negative_at < positive_at:
        raise ThresholdsInvalid(
            "The negative threshold must be lower than the positive threshold "
            f"(negative {negative_at} is not below positive {positive_at}).",
            "threshold_negative",
        )


def decide_top_label(
    distribution: Mapping[str, float], min_top: float, label_order: Sequence[str]
) -> str:
    """The top label when its probability is at least ``min_top``; otherwise ``excluded``.

    Ties go to the label that comes FIRST in ``label_order`` (the template's label set), so the
    answer never depends on dictionary order.
    """
    if not distribution:
        return "excluded"
    rank = {label: i for i, label in enumerate(label_order)}
    label, p = max(distribution.items(), key=lambda kv: (kv[1], -rank.get(kv[0], len(rank))))
    return label if p >= min_top else "excluded"


# --- keep share -------------------------------------------------------------------------------


def wilson_interval(kept: int, n: int, z: float = 1.959963984540054) -> tuple[float, float, float]:
    """(share, low, high): the Wilson score interval for ``kept`` of ``n`` (95% by default)."""
    if n <= 0:
        raise ValueError("a keep-share interval needs at least one scored row")
    if kept < 0 or kept > n:
        raise ValueError("kept must lie between 0 and n")
    share = kept / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = (share + z2 / (2 * n)) / denominator
    half = z * math.sqrt(share * (1.0 - share) / n + z2 / (4 * n * n)) / denominator
    return share, max(0.0, centre - half), min(1.0, centre + half)


# --- approval (P-07) --------------------------------------------------------------------------


def approval_needed(*, origin: str, rows_to_score: int, window_rows: int, threshold: int) -> bool:
    """An agent's rows on one version within the window, plus this run's, over the threshold.

    ``rows_to_score`` already excludes reused cached labels (P-07); a resume passes 0.
    Operator-origin runs are never gated.
    """
    return origin == "agent" and window_rows + rows_to_score > threshold


# --- identity, fingerprint, reuse (FR-005.28, FR-005.29) --------------------------------------


def labeler_identity(
    *,
    protocol: str,
    model_id: str,
    model_revision: str | None,
    template_ref: str,
    question: str | None,
) -> dict[str, Any]:
    """The labeler identity feature 006 keys calibration on. A missing revision reads
    "not reported"; it is never filled in."""
    return {
        "protocol": protocol,
        "model_id": model_id,
        "model_revision": model_revision if model_revision is not None else NOT_REPORTED,
        "template": template_ref,
        "question": question,
    }


def identity_hash(identity: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(identity))


def fingerprint(
    identity: Mapping[str, Any],
    sampling: Mapping[str, Any],
    structured_output: str,
    packing: str,
) -> str:
    """Identity plus everything else that changes a probability. Thresholds are NOT included:
    reuse copies probabilities and re-applies this run's thresholds."""
    return canonical_sha256(
        {
            "identity": dict(identity),
            "sampling": dict(sampling),
            "structured_output": structured_output,
            "packing": packing,
        }
    )


def reuse_allowed(this_revision: str | None, other_revision: str | None) -> bool:
    """Only when BOTH runs reported the same revision: without one, sameness cannot be shown."""
    return this_revision is not None and this_revision == other_revision


# --- judge controls ---------------------------------------------------------------------------


def swap_and_agree(first: str | None, second_swapped: str | None) -> str:
    """Pairwise verdicts in both orders (``second_swapped`` already mapped back). Agreement gives
    the verdict; disagreement is ``position_inconsistent``; an unparsed side is a parse failure."""
    if first is None or second_swapped is None:
        return "parse_failure"
    return first if first == second_swapped else "position_inconsistent"


def parse_failure_exceeded(failures: int, judged: int, share: float, min_rows: int) -> bool:
    """Stop once at least ``min_rows`` were judged and the failure share is ABOVE ``share``."""
    return judged >= min_rows and failures / judged > share


def aggregate_verdict(verdicts: Sequence[str | None]) -> tuple[str, str | None]:
    """(verdict, flag) over several judges: unanimous, majority (flagged), or a tie (excluded,
    flagged). Missing verdicts do not vote; no votes at all is excluded."""
    votes = Counter(v for v in verdicts if v is not None)
    if not votes:
        return "excluded", "no_verdicts"
    ranked = votes.most_common()
    if len(ranked) == 1:
        voted = sum(1 for v in verdicts if v is not None)
        return ranked[0][0], (None if voted == len(verdicts) else "missing_verdict")
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "excluded", "disagreement"
    return ranked[0][0], "disagreement"
