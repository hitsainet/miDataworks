"""Which decisions are allowed, what they must carry, and what a history means (FR-006.23 –
FR-006.25; P-10, T-29; FTID 006 section 7.8).

Guarantees:
- agents may record ``accept`` and ``flag`` only (P-10); ``reject`` exists on external queues only
  (T-29). :func:`allowed` is called by the decision route BEFORE anything is written, and a test
  asserts the call by walking the route's AST;
- :func:`effective_state` folds a decision history oldest first. Only an OPERATOR override changes
  the label; an agent accept changes nothing; any flag leaves the row flagged until an operator
  decides again. An agent override cannot win even if one were stored.

No input/output: plain values in, a value or a :class:`DecisionRefused` out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

DECISIONS: tuple[str, ...] = ("accept", "override", "flag", "reject")
QUEUE_KINDS: tuple[str, ...] = ("label_review", "calibration_labeling", "audit", "external")
QUEUE_STATES: tuple[str, ...] = ("open", "closed")
AGENT_DECISIONS: frozenset[str] = frozenset({"accept", "flag"})  # P-10
NEEDS_REASON: frozenset[str] = frozenset({"override", "flag", "reject"})
DEFAULT_ACCEPT_REASON = "accepted the model label"
DEFAULT_ASSIGN_REASON = "label assigned by the reviewer"

State = Literal["model", "overridden", "flagged_unresolved", "rejected"]


class DecisionRefused(ValueError):
    """A refused decision with its error code and HTTP status."""

    def __init__(
        self, message: str, code: str, status: int, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}


def allowed(decision: str, origin: str, queue_kind: str) -> None:
    """Refuse a decision this caller may not make on this queue."""
    if decision not in DECISIONS:
        raise DecisionRefused(
            f"Unknown decision {decision!r}; use one of {list(DECISIONS)}.",
            "DECISION_INVALID",
            422,
        )
    if decision == "reject" and queue_kind != "external":
        raise DecisionRefused(
            "Reject exists only for external candidates (miForge). Use Override label or Flag row.",
            "DECISION_INVALID",
            422,
            {"queue_kind": queue_kind},
        )
    if origin == "agent" and decision not in AGENT_DECISIONS:
        raise DecisionRefused(
            f"Agents can accept or flag a row but not {decision} it (P-10). Flag it with a reason "
            "and the operator will decide.",
            "AGENT_DECISION_NOT_ALLOWED",
            403,
            {"allowed": sorted(AGENT_DECISIONS)},
        )


def validate(
    decision: str,
    *,
    override_label: str | None,
    reason: str | None,
    label_set: Sequence[str],
    queue_kind: str,
    model_output_visible: bool,
) -> str:
    """Check what the decision carries; returns the reason to store."""
    text = (reason or "").strip()
    if decision == "override":
        if override_label is None or override_label not in label_set:
            raise DecisionRefused(
                f"Choose a label from this question's label set: {list(label_set)}.",
                "DECISION_INVALID",
                422,
                {"allowed_labels": list(label_set)},
            )
    elif override_label is not None:
        raise DecisionRefused(
            "Only an override carries a label.", "DECISION_INVALID", 422, {"decision": decision}
        )
    if queue_kind == "calibration_labeling":
        if decision == "accept" and not model_output_visible:
            raise DecisionRefused(
                "Model output is hidden in this queue, so there is no model label to accept. "
                "Assign the label yourself.",
                "DECISION_INVALID",
                422,
            )
        if decision == "override" and not text:
            return DEFAULT_ASSIGN_REASON
    if decision in NEEDS_REASON and not text:
        raise DecisionRefused(
            f"Write a reason for this {decision}; it is kept with the decision.",
            "DECISION_INVALID",
            422,
            {"field": "reason"},
        )
    if decision == "accept" and not text:
        return DEFAULT_ACCEPT_REASON
    return text


@dataclass(frozen=True)
class EffectiveState:
    label: Any
    state: State
    decision_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "state": self.state, "decision_id": self.decision_id}

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        """The shape 008's seam reads (``feature_seams._dump``)."""
        return self.as_dict()


def effective_state(history: Iterable[Any], model_label: Any) -> EffectiveState:
    """Fold a decision history, OLDEST FIRST. Each item has ``id``, ``decision``,
    ``override_label`` and ``decided_by_origin`` (rows or mappings)."""

    def get(item: Any, name: str) -> Any:
        return item[name] if isinstance(item, dict) else getattr(item, name)

    current = EffectiveState(model_label, "model", None)
    for item in history:
        decision = get(item, "decision")
        origin = get(item, "decided_by_origin")
        decision_id = str(get(item, "id"))
        if decision == "flag":
            current = EffectiveState(None, "flagged_unresolved", decision_id)
        elif origin != "operator":
            continue  # an agent accept changes nothing; an agent override never wins (P-10)
        elif decision == "override":
            current = EffectiveState(get(item, "override_label"), "overridden", decision_id)
        elif decision == "accept":
            current = EffectiveState(model_label, "model", decision_id)
        elif decision == "reject":
            current = EffectiveState(None, "rejected", decision_id)
    return current
