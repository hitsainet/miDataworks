"""The two client interfaces and their results (FTID 005 section 3.1).

``ClassifierClient.score`` returns a probability distribution over the template's label set;
``JudgeClient.judge`` returns a verdict (or none) and a rationale. Every client sends through
``clients/endpoint_caller.py``; the typed outcomes are re-exported here for the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..endpoint_errors import (
    Backpressure,
    ContextOverflow,
    EndpointCallError,
    LeaseLost,
    ModelNotResident,
    ProtocolUnsupported,
    RowError,
    StrictRefusal,
    TransientError,
)

__all__ = [
    "Backpressure",
    "ClassifierClient",
    "ContextOverflow",
    "EndpointCallError",
    "JudgeClient",
    "JudgeResult",
    "LeaseLost",
    "ModelNotResident",
    "ProtocolUnsupported",
    "RenderedInput",
    "RowError",
    "ScoreResult",
    "StrictRefusal",
    "TransientError",
]


@dataclass(frozen=True)
class RenderedInput:
    """One row as a client sees it: the template's input fields and the run's question."""

    row_key: str
    fields: dict[str, Any]
    question: str | None


@dataclass(frozen=True)
class ScoreResult:
    #: label -> probability over the template's label set; sums to 1.
    distribution: dict[str, float]
    #: The response's logprobs or scores, verbatim.
    raw_output: Any
    latency_ms: int
    response_model: str | None
    #: ``X-miLLM-Steering`` verbatim, or None when the server sent none.
    steering_header: str | None
    system_fingerprint: str | None
    prompt_tokens: int | None
    #: The exact request body sent (FR-005.27: the run and the row rebuild it byte-identically).
    request_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class JudgeResult:
    verdict: str | None
    #: Pairwise only: the verdict asked in the swapped order, already mapped back.
    swapped_verdict: str | None
    score: float | None
    rationale: str | None
    raw_output: Any
    parse_ok: bool
    latency_ms: int
    #: ``X-miLLM-Seed`` verbatim when echoed, else None ("seed not confirmed").
    seed_echo: str | None
    steering_header: str | None
    response_model: str | None
    system_fingerprint: str | None
    pairwise: bool = False


@runtime_checkable
class ClassifierClient(Protocol):
    def score(self, row: RenderedInput) -> ScoreResult: ...


@runtime_checkable
class JudgeClient(Protocol):
    def judge(self, row: RenderedInput) -> JudgeResult: ...
