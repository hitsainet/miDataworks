"""miLLM per-request SAE activations in scoring mode (miLLM Feature 27, FR-27.1 - FR-27.3; 009
FR-009.65 - FR-009.68).

Served at miLLM ``44e4c4a`` (``millm/services/request_activations.py``), so this client is built
(ADR-027; 009 FTASKS 12.1 records the search). One ``POST /v1/completions`` per row in SCORING
mode (``logprobs`` set, ``max_tokens: 1``): miLLM's one scorer (``_score_texts``) runs the forward
with every attached SAE suppressed and reports the read point as ``unsteered`` (FR-27.3a; X-09).
Nothing is generated. The etiquette is 005's ``EndpointCaller``'s: strict, refuse-load, the lease.

The response's ``millm.sae_activations`` block is ``{sae_id, layer, read_point, positions:
[{position, token_id, features: [{index, value}]}], note}``, read directly — a missing block, when
one was asked for, is a contract break the caller names.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..endpoint_caller import EndpointCaller
from .base import RowError

PATH = "/v1/completions"


@dataclass(frozen=True)
class Activations:
    block: dict[str, Any] | None
    model: str | None
    prompt_tokens: int | None
    steering_header: str | None
    latency_ms: int
    request_body: dict[str, Any]


class SaeActivationsClient:
    def __init__(
        self,
        caller: EndpointCaller,
        model: str,
        *,
        sae_id: str,
        top_k: int,
        positions: str,
        features: Sequence[int] | None = None,
        lease_id: str | None = None,
    ) -> None:
        self.caller = caller
        self.model = model
        self.spec: dict[str, Any] = {"sae_id": sae_id, "top_k": int(top_k), "positions": positions}
        if features is not None:
            self.spec["features"] = [int(f) for f in features]
        self.lease_id = lease_id

    def request_body(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        text = fields.get("text")
        if not isinstance(text, str) or not text:
            raise RowError("the row has no text to tag", 422)
        return {
            "model": self.model,
            "prompt": text,
            "max_tokens": 1,
            "logprobs": 1,
            "return_sae_activations": dict(self.spec),
        }

    def read(self, fields: Mapping[str, Any]) -> Activations:
        body = self.request_body(fields)
        response = self.caller.call(
            "POST", PATH, body=body, purpose="scoring", openai=True, lease_id=self.lease_id
        )
        payload = response.body if isinstance(response.body, dict) else {}
        millm = payload.get("millm") or {}
        usage = payload.get("usage") or {}
        return Activations(
            block=millm.get("sae_activations"),
            model=payload.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            steering_header=response.header("X-miLLM-Steering"),
            latency_ms=response.latency_ms,
            request_body=body,
        )
