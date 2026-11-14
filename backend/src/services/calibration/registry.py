"""The metric registry: the only way a figure reaches a gate (FR-006.14, FR-006.39; FTDD 006 §6.2).

Guarantees:
- a spec with a ``gate`` and no checks is refused, and so is a check ID not in ``checks.CHECKS``;
- the verdict reads gate metrics from here, never from a literal list;
- other features (007 for gate 3) call :func:`register_metric` from their own module; this module
  never imports them.

``invalidates_verdict`` separates two consequences of a failed check on a gate metric. A failed
check on ``auroc`` makes the verdict ``invalid`` (FR-006.16, FR-006.18 step 1). A failed check on
the ceiling or its comparison marks the CEILING invalid and unused, and the verdict falls back to
the other rules (FPRD 006 section 2.3, "A held-out rater scores AUROC >= 0.99": "the ceiling is
marked invalid and not used by the gate"). The two FPRD passages conflict for the ceiling; the
specific edge case wins, recorded in the implementation-controls review.
"""

from __future__ import annotations

from dataclasses import dataclass

from .checks import CHECKS, CheckContext, CheckResult


@dataclass(frozen=True)
class MetricSpec:
    metric_id: str
    #: ``gate2`` for gate metrics; ``gate3`` for 007's figures; None for a diagnostic.
    gate: str | None
    checks: tuple[str, ...]
    invalidates_verdict: bool = True


METRIC_REGISTRY: dict[str, MetricSpec] = {}


def register_metric(spec: MetricSpec) -> MetricSpec:
    """Add a spec, refusing a gate metric without checks or with an unknown check ID."""
    if spec.gate is not None and not spec.checks:
        raise ValueError(f"gate metric {spec.metric_id!r} must name at least one sanity check")
    unknown = [c for c in spec.checks if c not in CHECKS]
    if unknown:
        raise ValueError(f"metric {spec.metric_id!r} names unknown check(s) {unknown}")
    existing = METRIC_REGISTRY.get(spec.metric_id)
    if existing is not None and existing != spec:
        raise ValueError(f"metric {spec.metric_id!r} is already registered differently")
    METRIC_REGISTRY[spec.metric_id] = spec
    return spec


def gate_metrics(gate: str) -> list[MetricSpec]:
    return [s for s in METRIC_REGISTRY.values() if s.gate == gate]


def gate_metric_ids(gate: str = "gate2") -> set[str]:
    return {s.metric_id for s in gate_metrics(gate)}


def invalidating_metric_ids(gate: str = "gate2") -> set[str]:
    return {s.metric_id for s in gate_metrics(gate) if s.invalidates_verdict}


def run_checks(ctx: CheckContext, present: set[str]) -> list[CheckResult]:
    """Run every registered check for every metric whose figure was computed, in registry order.

    ``present`` names the metrics that were ATTEMPTED: ``auroc`` always, ``ceiling`` and
    ``comparison_auroc`` when a ratings column is declared, and so on. An attempted metric whose
    figure could not be computed still runs its checks, which say why.
    """
    results: list[CheckResult] = []
    for spec in METRIC_REGISTRY.values():
        if spec.gate not in (None, "gate2"):
            continue  # another gate's figures are checked by the feature that computes them
        if spec.metric_id not in present:
            continue
        for check_id in spec.checks:
            results.append(CHECKS[check_id].fn(ctx, spec.metric_id))
    return results


# --- version 1 gate-2 entries (FTDD 006 section 6.2) ----------------------------------------
register_metric(
    MetricSpec(
        "auroc",
        "gate2",
        ("both_classes", "label_permutation_null", "row_alignment", "shortcut_comparison"),
    )
)
register_metric(
    MetricSpec(
        "ceiling", "gate2", ("ceiling_circularity", "both_classes"), invalidates_verdict=False
    )
)
register_metric(
    MetricSpec("comparison_auroc", "gate2", ("row_alignment",), invalidates_verdict=False)
)
# --- diagnostics (never a gate metric) ------------------------------------------------------
register_metric(MetricSpec("paired", None, ("negative_class_control",)))
register_metric(MetricSpec("reference_diagnostic", None, ("negative_class_control",)))
