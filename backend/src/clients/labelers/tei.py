"""``tei_classification``: Hugging Face text classification on TEI (FR-005.6, FR-005.15; P-24).

``POST /predict {"inputs": <text>, "raw_scores": false, "truncate": false}``; the template's
``label_map`` maps the model's own labels to the run's. Every raw score is kept. ``GET /info``
gives the identity (``model_id``, ``model_sha``), checked against the run at every chunk boundary.

TEI is an external endpoint the operator runs on the GPU node; no TEI server existed when this was
written, so the fixtures under ``tests/fixtures/labeling/tei/`` are shaped from TEI's documented API
and flagged unverified (FTASKS 1.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...schemas.labeling import TEIClassificationTemplate
from ..endpoint_caller import EndpointCaller
from .base import ProtocolUnsupported, RenderedInput, RowError, ScoreResult
from .renderers import render


@dataclass(frozen=True)
class TEIIdentity:
    model_id: str | None
    model_sha: str | None


def read_identity(caller: EndpointCaller) -> TEIIdentity:
    response = caller.call("GET", "/info", purpose="probe", openai=False)
    body = response.body if isinstance(response.body, dict) else {}
    model_id = body.get("model_id")
    sha = body.get("model_sha")
    if not isinstance(model_id, str) or not model_id:
        raise ProtocolUnsupported("the server's /info names no model_id; it is not TEI")
    return TEIIdentity(model_id, sha if isinstance(sha, str) and sha else None)


class TEIClassifierClient:
    def __init__(
        self, caller: EndpointCaller, template: TEIClassificationTemplate, model: str
    ) -> None:
        self.caller = caller
        self.template = template
        self.model = model

    def request_body(self, row: RenderedInput) -> dict[str, Any]:
        return {
            "inputs": render(self.template.render, row.fields, row.question),
            "raw_scores": False,
            "truncate": False,
        }

    def score(self, row: RenderedInput) -> ScoreResult:
        body = self.request_body(row)
        response = self.caller.call("POST", "/predict", body=body, purpose="scoring", openai=False)
        scores = response.body
        if isinstance(scores, list) and scores and isinstance(scores[0], list):
            scores = scores[0]
        if not isinstance(scores, list) or not all(
            isinstance(s, dict) and "label" in s and "score" in s for s in scores
        ):
            raise ProtocolUnsupported("/predict did not return [{label, score}, ...]")
        distribution = dict.fromkeys(self.template.label_set, 0.0)
        unmapped = []
        for item in scores:
            mapped = self.template.label_map.get(str(item["label"]))
            if mapped is None:
                unmapped.append(str(item["label"]))
                continue
            distribution[mapped] += float(item["score"])
        if unmapped and not any(distribution.values()):
            raise RowError(f"no model label maps to the run's labels: {unmapped}")
        return ScoreResult(
            distribution=distribution,
            raw_output={"scores": scores},
            latency_ms=response.latency_ms,
            response_model=None,
            steering_header=None,
            system_fingerprint=None,
            prompt_tokens=None,
            request_body=body,
        )
