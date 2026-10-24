"""Warning copy (FR-004.35): column, figure, chance, scale, sample, and what to do next.

Pure. The model sentence, from the mockup: *"Format predicts the label 88.5% of the time. Humorous
is 89% jokes and not humorous is 88% news. A probe can score well here by detecting format.
Evaluate on an all-headline set, or build the format-balanced version."* The builder states the
same facts from the audit's own per-value counts, plus the scale and sample the mockup sentence
leaves implicit (R-03.62).
"""

from __future__ import annotations

from typing import Any


def _pretty(name: str) -> str:
    text = name.lstrip("_").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def dominant_values(column_result: dict[str, Any]) -> list[tuple[str, str, float]]:
    """Per label: the value holding the largest share of that label's rows, and the share."""
    per_value = column_result.get("per_value", {}).get("values", [])
    totals: dict[str, int] = {}
    best: dict[str, tuple[str, int]] = {}
    for item in per_value:
        for label, count in item["counts_by_label"].items():
            totals[label] = totals.get(label, 0) + int(count)
            if count > best.get(label, ("", -1))[1]:
                best[label] = (item["value"], int(count))
    other = column_result.get("per_value", {}).get("other") or {}
    for label, count in other.items():
        totals[label] = totals.get(label, 0) + int(count)
    out = []
    for label in column_result.get("classes", sorted(totals)):
        if label in best and totals.get(label):
            value, count = best[label]
            out.append((label, value, count / totals[label]))
    return out


def warning_copy(column: str, column_result: dict[str, Any], figure: float) -> str:
    name = _pretty(column)
    chance = float(column_result["chance"])
    n = int(column_result["n_rows"])
    parts = [
        f"{name} predicts the label {figure:.1%} of the time "
        f"(held-out balanced accuracy; chance is {chance:.0%}; on {n:,} rows)."
    ]
    shares = [
        f"{label.replace('_', ' ')} is {share:.0%} {value}"
        for label, value, share in dominant_values(column_result)
    ]
    if shares:
        sentence = " and ".join(shares)
        parts.append(sentence[:1].upper() + sentence[1:] + ".")
    lower = name.lower()
    parts.append(f"A probe can score well here by detecting {lower}.")
    parts.append(
        f"Evaluate on a set where {lower} does not vary, or build the {lower}-balanced version."
    )
    return " ".join(parts)
