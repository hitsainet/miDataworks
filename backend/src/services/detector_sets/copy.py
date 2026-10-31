"""Check reasons and next steps: one place for R-03.62 wording (FTID 009 section 11).

"Calibration negatives" is always written in full (FPRD section 4.2). Every number names its scale
and sample. No sentence claims a probe is causal, safe, guaranteed or validated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .length import OverlapResult
from .role_mapping import ROLE_WORDS


def roles_incomplete(
    missing: Sequence[str], no_ood: bool, bad_versions: Sequence[str], no_monitored: bool = False
) -> str:
    parts: list[str] = []
    if missing:
        parts.append("needs exactly one " + ", one ".join(ROLE_WORDS[m] for m in missing) + " role")
    if no_ood:
        parts.append("needs at least one out-of-distribution evaluation role")
    if bad_versions:
        parts.append("binds versions that are not completed: " + ", ".join(bad_versions))
    if no_monitored:
        parts.append("names no monitored text (the text the probe will watch)")
    return "The set " + "; ".join(parts) + "."


def shortcut_warning(w: Mapping[str, Any]) -> str:
    column = w.get("column", "a metadata column")
    figure = w.get("figure")
    level = w.get("level", w.get("margin_pp"))
    source = w.get("level_source")
    text = f"Column {column!r} predicts the label"
    if figure is not None:
        text += f" (held-out score {float(figure):.3f}"
        if level is not None:
            text += f" against a warning level of {float(level):.2f} points"
        if source:
            text += f", level from {source}"
        text += ")"
    return text + ". A probe trained on these rows could learn the column instead of the concept."


def excluded_from_audit(excluded: Sequence[Mapping[str, Any]]) -> str:
    """Name every column D-3 did not audit because it is the label's provenance, and why."""
    if not excluded:
        return ""
    parts: list[str] = []
    for e in excluded:
        column = e.get("column")
        if e.get("reason") == "pair_construction":
            parts.append(
                f"{column!r} (pair construction by {e.get('source_operator')}: it records which "
                "row is the seed and which the generated counterpart)"
            )
        elif e.get("reason") == "label_source":
            parts.append(
                f"{column!r} (the label was computed from it; declared by "
                f"{e.get('declared_by')})"
            )
        else:
            parts.append(f"{column!r} (written beside the label by {e.get('source_operator')})")
    return "Not audited, because the label comes from them: " + ", ".join(parts) + "."


def length_mismatch(ov: OverlapResult, tolerance: float) -> str:
    return (
        f"Only {ov.figure:.1%} of lengths overlap (tolerance {tolerance:.1%}): calibration "
        f"negatives run {ov.cal_range[0]:.0f}-{ov.cal_range[1]:.0f} characters (5th-95th "
        f"percentile) against {ov.ref_range[0]:.0f}-{ov.ref_range[1]:.0f} for the monitored text. "
        "A threshold cut on text of a different length fires at a different rate on the "
        "monitored text."
    )


def calibration_refusal(name: str, verdict: str, roles: Sequence[str]) -> str:
    used = ", ".join(roles)
    if verdict == "none":
        return f"Labeler {name} has no calibration record; its labels are used by {used}."
    return (
        f"Labeler {name}'s latest calibration verdict is {verdict}, which cannot support a "
        f"detector set; its labels are used by {used}."
    )


#: Words no probe-facing sentence may use (FTASKS 9.8).
FORBIDDEN_WORDS: tuple[str, ...] = ("causal", "safe", "guarantee", "validated")


# --- D-9 "wording alone" (015 FR-015.16) -------------------------------------------------------

_N_NOTE = (
    "An AUROC (area under the ROC curve) from words alone depends on the number of rows, so a "
    "figure is comparable only with another at the same size."
)


def wording_skipped() -> str:
    return (
        "Not computed: no training or in-distribution test role is bound to a completed version. "
        "The wording check runs once they are."
    )


def wording_green(facts: Sequence[Any]) -> str:
    compared = [f for f in facts if f.comparisons]
    base = (
        "A bag-of-words classifier (TF-IDF word 1- and 2-grams, logistic regression, five folds) "
        "does not separate the labels unusually well from the wording alone."
    )
    if compared:
        base += " Each figure was compared with the out-of-distribution roles at the same size."
    return f"{base} {_N_NOTE} Computed once per version and cached."


def wording_note(
    warned: Sequence[Any], missing: Sequence[Any], margin: float, absolute: float
) -> str:
    parts: list[str] = []
    for f in warned:
        if f.comparisons:
            refs = ", ".join(
                f"{c.reference} {c.reference_auroc:.3f} (role {c.auroc:.3f}, n={c.n:,})"
                for c in f.comparisons
            )
            parts.append(
                f"{f.name}: the wording alone separates the labels more than {margin:.2f} better "
                f"than every reference at the same size ({refs})."
            )
        else:
            parts.append(
                f"{f.name}: the wording alone separates the labels at {f.auroc:.3f} "
                f"(n={f.n:,}), at or above {absolute:.2f}, with no reference to compare against."
            )
    for f in missing:
        why = {
            "too_few_rows": "fewer than 20 rows of either class",
            "too_few_groups": "fewer pairs than folds",
        }.get(str(f.reason), str(f.reason))
        parts.append(f"{f.name}: not computed ({why}).")
    return " ".join(parts) + f" This is a warning, never a refusal. {_N_NOTE}"


def wording_next_step() -> str:
    return (
        "Look for words or phrases that only one class uses (template phrases, topics, length "
        "habits); a probe trained here may learn the wording, not the behaviour."
    )
