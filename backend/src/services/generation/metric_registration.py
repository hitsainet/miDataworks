"""Feature 007's gate-3 figures in 006's metric registry (FR-006.14; FR-007.41; FTASKS 9.7).

006's registry refuses a gate metric without sanity checks, and refuses a check it does not know,
so the two diversity controls are added to 006's ``CHECKS`` first:

- ``diversity_negative_control``: a collapsed sample (half the rows replaced by copies from the
  largest cluster) MUST "fall" — else the figure cannot see narrowing;
- ``diversity_positive_control`` (version 2 since 2026-10-19): over eight random halves of the
  reference, a figure's halves MUST NOT differ by more than its own intervals allow — else the
  figure calls noise narrowing. Version 1 asked one split not to "fall" (FR-007.41).

The diversity report runs both controls itself (``diversity_service``); 006's ``run_checks``
skips gate-3 figures ("another gate's figures are checked by the feature that computes them"), so
the check functions here only ever answer for a calibration context, which never names them.
"""

from __future__ import annotations

from collections.abc import Callable

from ..calibration import checks as calibration_checks
from ..calibration.checks import CheckContext, CheckResult, CheckSpec
from ..calibration.registry import MetricSpec, register_metric

NEGATIVE = "diversity_negative_control"
POSITIVE = "diversity_positive_control"
GATE = "gate3"
METRICS: tuple[str, ...] = ("distinct_1", "distinct_2", "embedding_spread", "cluster_coverage")


def _computed_by_007(check_id: str) -> Callable[[CheckContext, str], CheckResult]:
    """The check function for ``check_id``. Until 2026-10-19 one function served both controls
    and chose its id by ``metric_id.endswith("negative")``, which no gate-3 metric id does, so
    the negative control answered as the positive one."""

    def check(ctx: CheckContext, metric_id: str) -> CheckResult:
        del ctx
        return CheckResult(
            check_id,
            calibration_checks.CHECKS[check_id].version,
            metric_id,
            "not_applicable",
            {},
            "computed by feature 007's diversity report, not by a calibration record",
            "gate-3 figures are checked where they are computed",
        )

    return check


CONTROL_SPECS: tuple[CheckSpec, ...] = (
    CheckSpec(
        NEGATIVE,
        1,
        "A diversity figure falls on a deliberately collapsed sample",
        _computed_by_007(NEGATIVE),
    ),
    CheckSpec(
        POSITIVE,
        2,
        "A diversity figure's halves differ no more than its own intervals allow, over eight "
        "random halves of its reference (split_half_calibration_v2)",
        _computed_by_007(POSITIVE),
    ),
)


def register() -> list[MetricSpec]:
    """Add the two controls and the four gate-3 metrics (idempotent)."""
    for spec in CONTROL_SPECS:
        existing = calibration_checks.CHECKS.get(spec.check_id)
        if existing is None:
            calibration_checks.CHECKS[spec.check_id] = spec
    return [register_metric(MetricSpec(metric, GATE, (NEGATIVE, POSITIVE))) for metric in METRICS]
