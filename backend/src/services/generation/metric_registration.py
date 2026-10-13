"""Feature 007's gate-3 figures in 006's metric registry (FR-006.14; FR-007.41; FTASKS 9.7).

006's registry refuses a gate metric without sanity checks, and refuses a check it does not know,
so the two diversity controls are added to 006's ``CHECKS`` first:

- ``diversity_negative_control``: a collapsed sample (half the rows replaced by copies from the
  largest cluster) MUST "fall" — else the figure cannot see narrowing;
- ``diversity_positive_control``: two halves of the reference MUST NOT "fall" — else the figure
  calls noise narrowing.

The diversity report runs both controls itself (``diversity_service``); 006's ``run_checks``
skips gate-3 figures ("another gate's figures are checked by the feature that computes them"), so
the check functions here only ever answer for a calibration context, which never names them.
"""

from __future__ import annotations

from ..calibration import checks as calibration_checks
from ..calibration.checks import CheckContext, CheckResult, CheckSpec
from ..calibration.registry import MetricSpec, register_metric

NEGATIVE = "diversity_negative_control"
POSITIVE = "diversity_positive_control"
GATE = "gate3"
METRICS: tuple[str, ...] = ("distinct_1", "distinct_2", "embedding_spread", "cluster_coverage")


def _computed_by_007(ctx: CheckContext, metric_id: str) -> CheckResult:
    del ctx
    return CheckResult(
        NEGATIVE if metric_id.endswith("negative") else POSITIVE,
        1,
        metric_id,
        "not_applicable",
        {},
        "computed by feature 007's diversity report, not by a calibration record",
        "gate-3 figures are checked where they are computed",
    )


CONTROL_SPECS: tuple[CheckSpec, ...] = (
    CheckSpec(
        NEGATIVE, 1, "A diversity figure falls on a deliberately collapsed sample", _computed_by_007
    ),
    CheckSpec(
        POSITIVE,
        1,
        "A diversity figure holds between two halves of its reference",
        _computed_by_007,
    ),
)


def register() -> list[MetricSpec]:
    """Add the two controls and the four gate-3 metrics (idempotent)."""
    for spec in CONTROL_SPECS:
        existing = calibration_checks.CHECKS.get(spec.check_id)
        if existing is None:
            calibration_checks.CHECKS[spec.check_id] = spec
    return [register_metric(MetricSpec(metric, GATE, (NEGATIVE, POSITIVE))) for metric in METRICS]
