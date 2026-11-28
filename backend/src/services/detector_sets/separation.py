"""P-21: is an evaluating detector separate from the one it evaluates (FR-009.71, FR-009.79)?

Two probes are separate when their IDs differ AND the row keys each was trained on are disjoint.
A probe whose training rows cannot be traced (report -> run -> train view -> 009's registration ->
version and split -> row keys) is ``unprovable``: the check FAILS CLOSED, never assumes separate.
Two probes on the same model and layer are separate but carry a warning.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["separate", "not_separate", "unprovable"]


@dataclass(frozen=True)
class ProbeTrace:
    probe_id: str
    model_id: str | None
    layer: int | None
    #: None when any link of the trace is missing.
    train_row_keys: Collection[str] | None
    missing_link: str | None = None


@dataclass(frozen=True)
class SeparationResult:
    verdict: Verdict
    reason: str
    warnings: list[str] = field(default_factory=list)
    shared_rows: int = 0


def separate(a: ProbeTrace, b: ProbeTrace) -> SeparationResult:
    if a.probe_id == b.probe_id:
        return SeparationResult("not_separate", "The two probes are the same probe.")
    for p in (a, b):
        if p.train_row_keys is None:
            return SeparationResult(
                "unprovable",
                f"Probe {p.probe_id}'s training rows cannot be traced"
                + (f" ({p.missing_link})" if p.missing_link else "")
                + "; separation is not assumed.",
            )
    assert a.train_row_keys is not None and b.train_row_keys is not None
    shared = len(set(a.train_row_keys) & set(b.train_row_keys))
    if shared:
        return SeparationResult(
            "not_separate",
            f"The probes share {shared:,} training rows.",
            shared_rows=shared,
        )
    warnings: list[str] = []
    if a.model_id is not None and a.model_id == b.model_id and a.layer == b.layer:
        warnings.append(
            f"Both probes read model {a.model_id} at layer {a.layer}; they may share a direction "
            "even on disjoint rows."
        )
    return SeparationResult("separate", "Different probes trained on disjoint rows.", warnings)
