"""Sanity checks: every metric shown as a quality claim says what would make it wrong (FR-006.14,
FR-006.15; FTID 006 section 7.6).

Guarantees: each check returns a :class:`CheckResult` with its identifier, version, the metric it
guards, a result (``pass``, ``fail``, ``not_applicable`` with a reason), its statistic and the rule
applied. ``CHECKS`` is the only list of checks; the metric registry names checks from it and a
registration naming an unknown check is refused.

No input/output. The shortcut comparison needs 004's held-out predictiveness figure
(FR-004.29); the caller passes ``None`` when 004 is not served, and the check then says so
(``not_applicable``) rather than passing (ADR-027).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from .ceiling import CeilingResult, Draw
from .constants import (
    CIRCULARITY_LIMIT,
    FIXED_POSITION_MIN_ROWS,
    PERMUTATIONS,
    ROW_ALIGNMENT_TOLERANCE,
    SEED_PAIRS,
    SEED_PERMUTATION,
)
from .metrics import Interval, ReferenceWins, auroc, auroc_fast, cluster_bootstrap_ci

Result = Literal["pass", "fail", "not_applicable"]


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    version: int
    metric_id: str
    result: Result
    statistic: dict[str, Any]
    rule: str
    reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "version": self.version,
            "metric_id": self.metric_id,
            "result": self.result,
            "statistic": self.statistic,
            "rule": self.rule,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PairedFigure:
    wins: NDArray[np.float64]
    group: NDArray[np.int64]
    interval: Interval


@dataclass(frozen=True)
class CheckContext:
    """Everything a check reads, built once per record from the loaded arrays."""

    #: Human labels (1/0) of the labeled, non-reference rows, in set position order.
    y: NDArray[np.int64]
    #: The labeler's scores for exactly those rows, loaded by the ordered join.
    scores: NDArray[np.float64]
    #: Row keys of exactly those rows.
    row_keys: Sequence[str]
    #: The same run's scores loaded a SECOND time, by row key (the row-alignment check).
    scores_by_key: Mapping[str, float]
    #: Row keys of every set row, in position order (draws index into this).
    all_row_keys: Sequence[str]
    auroc: Interval | None
    ceiling: CeilingResult | None = None
    draws: Sequence[Draw] | None = None
    paired: PairedFigure | None = None
    reference: ReferenceWins | None = None
    #: Per metadata column, 004's held-out figure against the human label; None = not served.
    shortcut: Mapping[str, float] | None = None
    shortcut_reason: str = "004's held-out predictiveness (FR-004.29) is not served"
    seeds: dict[str, int] = field(
        default_factory=lambda: {"permutation": SEED_PERMUTATION, "pairs": SEED_PAIRS}
    )


CheckFn = Callable[[CheckContext, str], CheckResult]


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    version: int
    guards: str
    fn: CheckFn


def _result(
    check_id: str,
    metric_id: str,
    result: Result,
    rule: str,
    statistic: dict[str, Any] | None = None,
    reason: str | None = None,
) -> CheckResult:
    return CheckResult(
        check_id, CHECKS[check_id].version, metric_id, result, statistic or {}, rule, reason
    )


def both_classes(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = "both human classes are present after mapping"
    if metric_id == "ceiling":
        usable = ctx.ceiling.draws if ctx.ceiling is not None else 0
        if usable == 0:
            return _result(
                "both_classes",
                metric_id,
                "fail",
                rule,
                {"usable_draws": 0},
                "no draw's consensus has both classes",
            )
        return _result("both_classes", metric_id, "pass", rule, {"usable_draws": usable})
    n_pos = int(np.sum(ctx.y))
    n_neg = int(len(ctx.y) - n_pos)
    stat = {"n_pos": n_pos, "n_neg": n_neg}
    if n_pos == 0 or n_neg == 0:
        missing = "positives" if n_pos == 0 else "negatives"
        return _result(
            "both_classes",
            metric_id,
            "fail",
            rule,
            stat,
            f"AUROC needs both classes; 0 {missing} after mapping",
        )
    return _result("both_classes", metric_id, "pass", rule, stat)


def label_permutation_null(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = "AUROC against permuted human labels has a 95% range that includes 0.5"
    if ctx.auroc is None:
        return _result(
            "label_permutation_null", metric_id, "not_applicable", rule, reason="no AUROC"
        )
    seed = ctx.seeds["permutation"]
    rng = np.random.default_rng(seed)
    stats = [auroc_fast(rng.permutation(ctx.y), ctx.scores) for _ in range(PERMUTATIONS)]
    lo, hi = (float(x) for x in np.quantile(stats, [0.025, 0.975]))
    stat = {"low": lo, "high": hi, "permutations": PERMUTATIONS, "seed": seed}
    if lo > 0.5 or hi < 0.5:
        return _result(
            "label_permutation_null",
            metric_id,
            "fail",
            rule,
            stat,
            f"permuted labels still give AUROC in [{lo:.3f}, {hi:.3f}], which excludes 0.5",
        )
    return _result("label_permutation_null", metric_id, "pass", rule, stat)


def row_alignment(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = "recomputing from the scores looked up by row key reproduces the reported value"
    try:
        if metric_id == "comparison_auroc":
            if ctx.ceiling is None or ctx.draws is None:
                return _result(
                    "row_alignment", metric_id, "not_applicable", rule, reason="no ceiling"
                )
            usable = [d for d in ctx.draws if 0 < int(d.consensus.sum()) < len(d.consensus)]
            values = [
                auroc_fast(
                    d.consensus,
                    np.array([ctx.scores_by_key[ctx.all_row_keys[int(i)]] for i in d.kept]),
                )
                for d in usable
            ]
            reported, recomputed = ctx.ceiling.comparison.value, float(np.mean(values))
        else:
            if ctx.auroc is None:
                return _result(
                    "row_alignment", metric_id, "not_applicable", rule, reason="no AUROC"
                )
            looked_up = np.array([ctx.scores_by_key[k] for k in ctx.row_keys])
            reported, recomputed = ctx.auroc.value, auroc(ctx.y, looked_up)
    except KeyError as exc:
        return _result(
            "row_alignment",
            metric_id,
            "fail",
            rule,
            {"missing_row_key": str(exc)},
            "a scored row is missing when scores are read by row key",
        )
    stat = {"reported": reported, "recomputed": recomputed}
    if abs(reported - recomputed) > ROW_ALIGNMENT_TOLERANCE:
        return _result(
            "row_alignment",
            metric_id,
            "fail",
            rule,
            stat,
            f"reported {reported:.6f} but scores read by row key give {recomputed:.6f}",
        )
    return _result("row_alignment", metric_id, "pass", rule, stat)


def ceiling_circularity(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = f"ceiling AUROC < {CIRCULARITY_LIMIT} and no draw holds one fixed position out"
    if ctx.ceiling is None or ctx.draws is None:
        return _result(
            "ceiling_circularity", metric_id, "not_applicable", rule, reason="no ceiling"
        )
    fixed = [
        i
        for i, d in enumerate(ctx.draws)
        if len(d.positions) >= FIXED_POSITION_MIN_ROWS and len(np.unique(d.positions)) == 1
    ]
    stat = {"ceiling": ctx.ceiling.ceiling.value, "fixed_position_draws": fixed}
    if fixed:
        return _result(
            "ceiling_circularity",
            metric_id,
            "fail",
            rule,
            stat,
            f"{len(fixed)} draw(s) held the same position out of every row",
        )
    if ctx.ceiling.ceiling.value >= CIRCULARITY_LIMIT:
        return _result(
            "ceiling_circularity",
            metric_id,
            "fail",
            rule,
            stat,
            f"the held-out rater scores {ctx.ceiling.ceiling.value:.3f}; it is part of its own "
            "consensus",
        )
    return _result("ceiling_circularity", metric_id, "pass", rule, stat)


def shortcut_comparison(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = "no metadata column alone predicts the human label at or above the labeler's AUROC"
    if ctx.shortcut is None:
        return _result(
            "shortcut_comparison", metric_id, "not_applicable", rule, reason=ctx.shortcut_reason
        )
    if ctx.auroc is None:
        return _result("shortcut_comparison", metric_id, "not_applicable", rule, reason="no AUROC")
    beating = {c: v for c, v in ctx.shortcut.items() if v >= ctx.auroc.value}
    stat = {"columns": dict(ctx.shortcut), "labeler_auroc": ctx.auroc.value}
    if beating:
        column = sorted(beating)[0]
        return _result(
            "shortcut_comparison",
            metric_id,
            "fail",
            rule,
            stat,
            f"column {column!r} alone reaches {beating[column]:.3f}",
        )
    return _result("shortcut_comparison", metric_id, "pass", rule, stat)


def negative_class_control(ctx: CheckContext, metric_id: str) -> CheckResult:
    rule = (
        "the same metric with negative rows in the positive slot has a 95% interval not above 0.5"
    )
    seed = ctx.seeds["pairs"]
    if metric_id == "paired":
        if ctx.paired is None or len(ctx.paired.wins) == 0:
            return _result(
                "negative_class_control", metric_id, "not_applicable", rule, reason="no pairs"
            )
        control = cluster_bootstrap_ci(1.0 - ctx.paired.wins, ctx.paired.group, seed=seed)
    else:
        if ctx.reference is None or len(ctx.reference.negative) == 0:
            return _result(
                "negative_class_control",
                metric_id,
                "not_applicable",
                rule,
                reason="no negative rows with a reference",
            )
        control = cluster_bootstrap_ci(
            ctx.reference.negative, ctx.reference.negative_group, seed=seed
        )
    stat = {"control": control.value, "control_ci": [control.ci_low, control.ci_high], "seed": seed}
    if control.ci_low > 0.5:
        return _result(
            "negative_class_control",
            metric_id,
            "fail",
            rule,
            stat,
            f"negative rows also win {control.value:.3f} of the time; the metric measures something "
            "other than the concept",
        )
    return _result("negative_class_control", metric_id, "pass", rule, stat)


#: The one list of checks (FR-006.15). Version bumps when a rule changes.
CHECKS: dict[str, CheckSpec] = {
    spec.check_id: spec
    for spec in (
        CheckSpec("both_classes", 1, "AUROC is defined", both_classes),
        CheckSpec(
            "label_permutation_null",
            1,
            "AUROC is not produced by row misalignment or leakage",
            label_permutation_null,
        ),
        CheckSpec(
            "row_alignment", 1, "Scores belong to the rows they are scored against", row_alignment
        ),
        CheckSpec(
            "ceiling_circularity",
            1,
            "The held-out rater is not part of its own consensus",
            ceiling_circularity,
        ),
        CheckSpec(
            "shortcut_comparison",
            1,
            "AUROC is not explained by a metadata column",
            shortcut_comparison,
        ),
        CheckSpec(
            "negative_class_control",
            1,
            "Paired and beats-its-reference metrics measure the concept, not a side effect",
            negative_class_control,
        ),
    )
}


def failed_metrics(results: Sequence[CheckResult]) -> set[str]:
    """Metrics marked "does not measure what it claims" (FR-006.16)."""
    return {r.metric_id for r in results if r.result == "fail"}
