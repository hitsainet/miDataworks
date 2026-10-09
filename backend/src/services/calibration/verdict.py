"""The gate-2 verdict for one calibration record (FR-006.18; P-16, C3, P-02; FTID 006 §7.7).

Rule, in order:
1. a failed check on a computed gate metric that invalidates the verdict -> ``invalid``;
2. no AUROC (one human class) -> ``insufficient``;
3. ``passes`` when (a) an operator target is set and the AUROC interval's LOWER bound reaches it,
   or (b) a valid ceiling exists and the comparison AUROC reaches the rater interval's LOWER
   bound; with no target and no valid ceiling, (a) uses C3's default 0.70 on the lower bound;
4. otherwise ``fails``, naming the rule tried.

Every comparison is ``>=`` (X-03: a value exactly on the bar passes). The verdict's enums are
exactly 008's ``CalibrationRef`` values. No input/output.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .checks import CheckResult
from .constants import C3_DEFAULT_LOWER_BOUND
from .metrics import Interval
from .registry import invalidating_metric_ids

VerdictName = Literal["passes", "fails", "invalid", "insufficient"]
RuleName = Literal["operator_target", "held_out_rater", "default_c3"]
VERDICTS: tuple[str, ...] = ("passes", "fails", "invalid", "insufficient")
RULES: tuple[str, ...] = ("operator_target", "held_out_rater", "default_c3")
CEILING_METRICS: frozenset[str] = frozenset({"ceiling", "comparison_auroc"})


@dataclass(frozen=True)
class Figures:
    auroc: Interval | None
    ceiling: Interval | None = None
    comparison: Interval | None = None


@dataclass(frozen=True)
class Verdict:
    verdict: VerdictName
    rule: RuleName | None
    numbers: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "rule": self.rule, "numbers": self.numbers}


def _interval(i: Interval | None) -> dict[str, float] | None:
    return None if i is None else {"value": i.value, "ci_low": i.ci_low, "ci_high": i.ci_high}


def ceiling_valid(figs: Figures, checks: Sequence[CheckResult]) -> bool:
    if figs.ceiling is None or figs.comparison is None:
        return False
    return not any(c.result == "fail" and c.metric_id in CEILING_METRICS for c in checks)


def decide(figs: Figures, checks: Sequence[CheckResult], target: float | None) -> Verdict:
    computed = {"auroc"} if figs.auroc is not None else set()
    if figs.ceiling is not None:
        computed.add("ceiling")
    if figs.comparison is not None:
        computed.add("comparison_auroc")
    failed = [
        c.check_id
        for c in checks
        if c.result == "fail"
        and c.metric_id in invalidating_metric_ids()
        and c.metric_id in computed
    ]
    valid_ceiling = ceiling_valid(figs, checks)
    numbers: dict[str, Any] = {
        "auroc": _interval(figs.auroc),
        "target": target,
        "c3_default_lower_bound": C3_DEFAULT_LOWER_BOUND,
        "ceiling": _interval(figs.ceiling),
        "comparison_auroc": _interval(figs.comparison),
        "ceiling_valid": valid_ceiling,
    }
    if failed:
        return Verdict("invalid", None, {**numbers, "failed_checks": failed})
    if figs.auroc is None:
        return Verdict("insufficient", None, numbers)
    if target is not None and figs.auroc.ci_low >= target:
        return Verdict(
            "passes",
            "operator_target",
            {**numbers, "compared": figs.auroc.ci_low, "threshold": target},
        )
    if valid_ceiling:
        assert figs.ceiling is not None and figs.comparison is not None
        if figs.comparison.value >= figs.ceiling.ci_low:
            return Verdict(
                "passes",
                "held_out_rater",
                {**numbers, "compared": figs.comparison.value, "threshold": figs.ceiling.ci_low},
            )
    if target is None and not valid_ceiling and figs.auroc.ci_low >= C3_DEFAULT_LOWER_BOUND:
        return Verdict(
            "passes",
            "default_c3",
            {**numbers, "compared": figs.auroc.ci_low, "threshold": C3_DEFAULT_LOWER_BOUND},
        )
    if target is not None:
        tried: RuleName = "operator_target"
        compared, threshold = figs.auroc.ci_low, target
    elif valid_ceiling:
        assert figs.ceiling is not None and figs.comparison is not None
        tried = "held_out_rater"
        compared, threshold = figs.comparison.value, figs.ceiling.ci_low
    else:
        tried = "default_c3"
        compared, threshold = figs.auroc.ci_low, C3_DEFAULT_LOWER_BOUND
    return Verdict("fails", tried, {**numbers, "compared": compared, "threshold": threshold})
