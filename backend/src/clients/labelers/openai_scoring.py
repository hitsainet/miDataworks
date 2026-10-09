"""``openai_scoring``: next-token log-probabilities restricted to verbalizer IDs (FR-005.6).

**Completions** (served by miLLM since 2026-10-04): the request is the prototype's body exactly
(``scripts/jev_client.py`` ``JevClient.noul``), in its field order — ``model``, ``prompt``,
``max_tokens: 1``, ``temperature: 1.0``, ``logprobs: <slot count>``, ``allowed_token_ids``,
``add_special_tokens`` (when the template sets it), ``return_tokens_as_token_ids: true`` — and
the answer is ``choices[0].logprobs.top_logprobs[0]`` keyed ``token_id:<id>``. ``top_logprobs``
is NEVER sent on completions (refused there, miLLM FR-25.3.3).

**Chat** (miLLM Feature 25, FR-25.5, FR-25.8; ADR-027 check recorded in FTASKS 1.3): ``messages``,
``logprobs: true``, ``top_logprobs: k``, ``allowed_token_ids``, ``return_tokens_as_token_ids``,
``max_tokens: 1``; the answer is ``choices[0].logprobs.content[0].top_logprobs[]``.

Steering: scoring on miLLM is always unsteered (X-09); the header is returned verbatim when present
and the engine records "unsteered (scoring mode)" otherwise.
"""

from __future__ import annotations

from typing import Any

from ...schemas.labeling import OpenAIScoringTemplate
from ..endpoint_caller import STEERING_HEADER, EndpointCaller
from .base import ProtocolUnsupported, RenderedInput, RowError, ScoreResult
from .jev import probability_from_logprobs
from .renderers import render


class OpenAIScoringClient:
    def __init__(
        self,
        caller: EndpointCaller,
        template: OpenAIScoringTemplate,
        model: str,
        *,
        lease_id: str | None = None,
    ) -> None:
        self.caller = caller
        self.template = template
        self.model = model
        self.lease_id = lease_id
        start, end = template.slots[template.decision_kind]
        self._ids = list(template.verbalizer_ids[start:end])

    def request_body(self, row: RenderedInput) -> dict[str, Any]:
        """The exact body sent for ``row`` (FR-005.27). Field order is the prototype's."""
        prompt = render(self.template.render, row.fields, row.question)
        if self.template.variant == "completions":
            body: dict[str, Any] = {
                "model": self.model,
                "prompt": prompt,
                "max_tokens": 1,
                "temperature": 1.0,
                "logprobs": len(self._ids),
                "allowed_token_ids": list(self._ids),
            }
            if "add_special_tokens" in self.template.tokenization:
                body["add_special_tokens"] = self.template.tokenization["add_special_tokens"]
            body["return_tokens_as_token_ids"] = True
            return body
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "temperature": 1.0,
            "logprobs": True,
            "top_logprobs": len(self._ids),
            "allowed_token_ids": list(self._ids),
            "return_tokens_as_token_ids": True,
        }

    def _top(self, body: Any) -> dict[str, float]:
        try:
            choice = body["choices"][0]
            logprobs = choice.get("logprobs")
        except (KeyError, IndexError, TypeError):
            raise ProtocolUnsupported("the response has no choices") from None
        if not logprobs:
            raise ProtocolUnsupported(
                "the server returned no logprobs; it does not serve scoring mode"
            )
        if self.template.variant == "completions":
            top = logprobs.get("top_logprobs")
            if not isinstance(top, list) or not top or not isinstance(top[0], dict):
                raise ProtocolUnsupported("the response has no top_logprobs[0]")
            return {str(k): float(v) for k, v in top[0].items()}
        content = logprobs.get("content")
        if not isinstance(content, list) or not content:
            raise ProtocolUnsupported("the response has no logprobs.content[0]")
        entries = content[0].get("top_logprobs") or []
        out: dict[str, float] = {}
        for entry in entries:
            if isinstance(entry, dict) and "token" in entry and "logprob" in entry:
                out[str(entry["token"])] = float(entry["logprob"])
        return out

    @property
    def path(self) -> str:
        return (
            "/v1/completions"
            if self.template.variant == "completions"
            else ("/v1/chat/completions")
        )

    def result_from(
        self,
        body: Any,
        *,
        latency_ms: int,
        steering_header: str | None,
        request_body: dict[str, Any],
    ) -> ScoreResult:
        """A response body (synchronous, or one batch output line's) as a score."""
        top = self._top(body)
        probs = probability_from_logprobs(
            top,
            verbalizer_ids=self.template.verbalizer_ids,
            slots=self.template.slots,
            bias=self.template.bias,
            temperature=self.template.temperature,
            kind=self.template.decision_kind,
        )
        if len(probs) != len(self.template.label_set):
            raise RowError("the decision slot does not match the label set")
        data = body if isinstance(body, dict) else {}
        usage = data.get("usage")
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        return ScoreResult(
            distribution=dict(zip(self.template.label_set, probs, strict=True)),
            raw_output={"top_logprobs": top},
            latency_ms=latency_ms,
            response_model=data.get("model"),
            steering_header=steering_header,
            system_fingerprint=data.get("system_fingerprint"),
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            request_body=request_body,
        )

    def score(self, row: RenderedInput) -> ScoreResult:
        body = self.request_body(row)
        response = self.caller.call(
            "POST", self.path, body=body, purpose="scoring", openai=True, lease_id=self.lease_id
        )
        return self.result_from(
            response.body,
            latency_ms=response.latency_ms,
            steering_header=response.header(STEERING_HEADER),
            request_body=body,
        )
