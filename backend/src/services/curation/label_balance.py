"""The label balancer's plan and its lineage record (FR-004.52–004.57; FTDD 004 §6.2a).

Separate from the cell balancer on purpose (operator decision 2026-10-17): resampling rows BY THEIR
LABEL changes what a model learns and how it is evaluated, so the step is explicit in the recipe and
its warning travels with the version, its descendants, the publish checks and the manifest.

- :func:`label_balance_plan` is pure: per split, per class, seeded undersampling. No row moves
  between splits, no row is duplicated (oversampling would put copies of one row on both sides of a
  split).
- :func:`label_balancing` lists every ``label_balancer`` step in a version and its ancestors.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

OPERATOR = "label_balancer"
WARNING_CODE = "label_balanced"
LEAKAGE_WARNING: dict[str, str] = {
    "code": WARNING_CODE,
    "message": (
        "Rows were selected by their label. The class balance of this version is artificial: a "
        "model or probe trained or evaluated on it is calibrated to the balanced prior, not the "
        "real one. Balancing after a split resamples the evaluation splits by the label, and "
        "dropping rows by label can create a new shortcut (see the re-audit)."
    ),
}


class LabelBalanceRefusal(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class LabelPlan:
    #: Per row: True when kept.
    kept: np.ndarray
    #: ``{split: cap}``.
    caps: dict[str, int]
    #: ``{split: {class: {"before": n, "after": m}}}``.
    classes: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)


def label_balance_plan(
    labels: Sequence[str],
    splits: Sequence[str],
    *,
    seed: int,
    classes: Sequence[str] | None = None,
    cap: int | None = None,
) -> LabelPlan:
    """Undersample every class to the smallest (or ``cap``) within each split separately."""
    if cap is not None and int(cap) < 1:
        raise LabelBalanceRefusal(
            "invalid_cap", "The per-class cap must be at least 1.", {"cap": cap}
        )
    n = len(labels)
    kept = np.ones(n, dtype=bool)
    by_split: dict[str, dict[str, list[int]]] = {}
    for i in range(n):
        by_split.setdefault(str(splits[i]), {}).setdefault(str(labels[i]), []).append(i)
    rng = np.random.default_rng(seed)
    caps: dict[str, int] = {}
    report: dict[str, dict[str, dict[str, int]]] = {}
    for split in sorted(by_split):
        members = by_split[split]
        expected = sorted(set(classes)) if classes else sorted(members)
        missing = [c for c in expected if not members.get(c)]
        if missing:
            raise LabelBalanceRefusal(
                "empty_class",
                f"The class {missing[0]!r} has no rows in split {split!r}, so every class would be "
                "capped at zero. Remove it from the expected classes, or label rows of that class.",
                {"class": missing[0], "split": split, "empty_classes": missing},
            )
        present = sorted(set(members) | set(expected))
        if len(present) < 2:
            raise LabelBalanceRefusal(
                "single_class_label",
                f"Split {split!r} has one class ({present[0]!r}); there is nothing to balance.",
                {"split": split, "classes": present},
            )
        split_cap = min(len(members[c]) for c in present)
        if cap is not None:
            split_cap = min(split_cap, int(cap))
        caps[split] = split_cap
        report[split] = {}
        for label in present:
            rows = members[label]  # already in (row key, occurrence) order
            order = rng.permutation(len(rows))
            for j in order[split_cap:]:
                kept[rows[j]] = False
            report[split][label] = {"before": len(rows), "after": min(len(rows), split_cap)}
    return LabelPlan(kept=kept, caps=caps, classes=report)


def label_balancing(session: Session, version_id: str) -> list[dict[str, Any]]:
    """Every label-balancer step in the version and its ancestors (FR-004.56), newest first."""
    from ...models.recipe import RecipeBody
    from ...models.step_execution import StepExecution
    from ...models.version import VersionStep
    from ..version_lineage import lineage_versions

    out: list[dict[str, Any]] = []
    for version in lineage_versions(session, version_id):
        rows = session.execute(
            select(VersionStep.step_index, StepExecution)
            .join(StepExecution, StepExecution.id == VersionStep.step_execution_id)
            .where(
                VersionStep.version_id == version.id,
                StepExecution.kind == "operator",
                StepExecution.operator_name == OPERATOR,
            )
            .order_by(VersionStep.step_index)
        ).all()
        if not rows:
            continue
        body = session.get(RecipeBody, version.recipe_hash) if version.recipe_hash else None
        steps = (body.body if body else {}).get("steps", [])
        for step_index, execution in rows:
            index = int(step_index) - 1  # step 0 is assemble
            params = steps[index].get("params", {}) if 0 <= index < len(steps) else {}
            out.append(
                {
                    "version_id": str(version.id),
                    "version_number": int(version.number),
                    "inherited": str(version.id) != str(version_id),
                    "step_index": int(step_index),
                    "operator_version": execution.operator_version,
                    "label_column": params.get("label_column") or "label",
                    "rows_dropped": execution.rows_dropped,
                    "warning": dict(LEAKAGE_WARNING),
                }
            )
    return out
