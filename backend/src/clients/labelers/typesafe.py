"""``typesafe_systemone``: TypeSafe's hosted JEV (System One) API, one ``choice`` question per row
(FR-005.56; operator decision D58, 2026-10-18).

``POST {origin}/v1/systemone`` with ``{"state", "model", "questions": {<id>: {"type": "choice",
"instructions", "criteria"}}}``; the answer's ``probabilities`` (one per label, summing to 1) is the
row's distribution. Sources, read 2026-10-18: ``https://docs.typesafe.ai/api.md``,
``/primitives/choice.md``, ``/models.md``, ``/sdk/python/api/retries.md``,
``/sdk/python/api/exceptions.md``.

- No ``X-miLLM-*`` header is sent (``etiquette=False``): TypeSafe is a third party.
- An alias model (``jev-latest``, ``jev-preview``) resolves to a version per response; that version
  is reported as the run's fingerprint, so the first response pins it and a change mid-run stops
  the run (the engine's existing ``system_fingerprint`` rule). A versioned id is reported as the
  response model, which the engine compares with the run's model.
- 429 and 5xx (529: overloaded) are retried by ``EndpointCaller`` (``Retry-After`` /
  ``retry-after-ms``); 401/403 refuse the key; other 4xx refuse the row.
- The key is the role's stored key (``Authorization: Bearer``), registered with the log redactor by
  ``EndpointCaller``; neither it nor the rendered state is logged here.
"""

from __future__ import annotations

from typing import Any

from ...schemas.labeling import TypeSafeSystemOneTemplate
from ..endpoint_caller import EndpointCaller
from .base import ProtocolUnsupported, RenderedInput, RowError, ScoreResult
from .renderers import render

PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
DEFAULT_MODEL = "jev-latest"
#: Aliases TypeSafe resolves to a version per request (``/models.md``, 2026-10-18).
ALIASES = frozenset({"jev-latest", "jev-preview"})
#: How close to 1 the probabilities must sum (they are reported to float precision).
SUM_TOLERANCE = 1e-3


class TypeSafeClient:
    def __init__(
        self, caller: EndpointCaller, template: TypeSafeSystemOneTemplate, model: str | None
    ) -> None:
        self.caller = caller
        self.template = template
        self.model = model or DEFAULT_MODEL

    def request_body(self, row: RenderedInput) -> dict[str, Any]:
        instructions = self.template.instructions.replace("{question}", row.question or "")
        return {
            "state": render(self.template.render, row.fields, row.question),
            "model": self.model,
            "questions": {
                self.template.question_id: {
                    "type": "choice",
                    "instructions": instructions,
                    # label_set's order (a stored body's criteria come back key-sorted)
                    "criteria": {
                        label: self.template.criteria[label] for label in self.template.label_set
                    },
                }
            },
        }

    def score(self, row: RenderedInput) -> ScoreResult:
        body = self.request_body(row)
        response = self.caller.call(
            "POST", PATH, body=body, purpose="scoring", openai=False, etiquette=False
        )
        data = response.body if isinstance(response.body, dict) else {}
        answers = data.get("answers")
        answer = answers.get(self.template.question_id) if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise ProtocolUnsupported(
                f"TypeSafe answered no choice under {self.template.question_id!r}"
            )
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, dict):
            raise ProtocolUnsupported("TypeSafe's choice answer has no probabilities")
        labels = list(self.template.label_set)
        if set(probabilities) != set(labels):
            raise RowError(
                f"TypeSafe answered labels {sorted(probabilities)}, not the template's {labels}"
            )
        distribution = {label: float(probabilities[label]) for label in labels}
        if abs(sum(distribution.values()) - 1.0) > SUM_TOLERANCE:
            raise RowError(f"TypeSafe's probabilities sum to {sum(distribution.values()):.4f}")
        model_field = data.get("model")
        returned = model_field if isinstance(model_field, str) else None
        usage_field = data.get("usage")
        usage: dict[str, Any] = usage_field if isinstance(usage_field, dict) else {}
        input_tokens = usage.get("input_tokens")
        return ScoreResult(
            distribution=distribution,
            raw_output={
                "choice": answer.get("choice"),
                "confidence": answer.get("confidence"),
                "probabilities": probabilities,
                "model": returned,
                "usage": usage,
                "request_id": response.header("x-typesafe-request-id"),
            },
            latency_ms=response.latency_ms,
            response_model=None if self.model in ALIASES else returned,
            steering_header=None,
            system_fingerprint=returned if self.model in ALIASES else None,
            prompt_tokens=input_tokens if isinstance(input_tokens, int) else None,
            request_body=body,
        )


def list_models(caller: EndpointCaller) -> list[str]:
    """``GET /v1/models`` names (aliases; versioned ids are accepted without being listed)."""
    response = caller.call("GET", MODELS_PATH, purpose="probe", openai=False, etiquette=False)
    body = response.body if isinstance(response.body, dict) else {}
    models = body.get("models")
    if not isinstance(models, list):
        raise ProtocolUnsupported('GET /v1/models did not answer {"models": [...]}; not TypeSafe')
    return [str(m["name"]) for m in models if isinstance(m, dict) and m.get("name")]
