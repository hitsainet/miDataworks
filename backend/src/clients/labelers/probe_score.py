"""miLLM ``POST /api/probes/score`` (miLLM Feature 27, FR-27.4 - FR-27.7; 009 FR-009.43 - FR-009.46).

Served at miLLM ``44e4c4a`` (``millm/api/routes/management/probes.py:322``; contract v1.10), so this
client is built (ADR-027 precondition, 009 FTASKS 12.1). It sends through 005's ``EndpointCaller``,
so the etiquette is the one place it lives: ``X-miLLM-Strict``, ``X-miLLM-Load-Policy: refuse``
(scoring is model work and must never load a model), ``X-miLLM-Lease`` when a ticket is held, and
``Retry-After`` on 503.

- ONE input per call (FR-009.46; miLLM FR-27.6b): each input runs in its own admission slot.
- Text rows are sent as ONE user turn (``messages``), never as ``text``: miLLM refuses ``text``
  until its one-user-turn render has reproduced a miStudio AUROC (T-49).
- ``return_token_ids: true`` so the tokens scored are recorded with the verdict.
- The response is miLLM's ``ApiResponse`` envelope: ``{success, data: {model, probes, skipped,
  results: [{index, verdicts: [...], error}]}}``. A verdict's ``verdict`` is true, false or null
  (null: the probe said nothing — never false); ``provisional`` and ``rung_language`` are kept as
  sent. Mapping to a label outcome is ``services/detector_sets/verdicts.map_verdict``.
- A refusal of the whole request (``PROBE_NOT_FOUND`` 404, ``PROBE_MODEL_MISMATCH`` /
  ``PROBE_DTYPE_MISMATCH`` / ``PROBE_HOOK_UNSUPPORTED`` / ``PROBE_NO_MODEL_LOADED`` 409,
  ``INVALID_PROBE_SCORE_REQUEST`` 400) arrives as 005's ``RowError`` carrying miLLM's code and
  details (``server_code``); ``services/probe_verdict_protocol.refusal_for`` names it.
- Response fields are read directly (``result["token_ids"]``), never through a defaulting
  ``get``: miLLM always sends them (``probe_scoring.ProbeScoringService.score``), so a missing
  one is a contract change that must fail loudly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..endpoint_caller import EndpointCaller
from .base import RowError

SCORE_PATH = "/api/probes/score"


@dataclass(frozen=True)
class ProbeScore:
    #: miLLM's verdicts for the one input (per probe and window), verbatim.
    verdicts: list[dict[str, Any]]
    token_ids: list[int] | None
    n_tokens: int | None
    model: dict[str, Any]
    skipped: list[dict[str, Any]]
    request_body: dict[str, Any]
    #: The input's own result, verbatim (``index``, ``input_kind``, ``prompt_tokens`` ...).
    result: dict[str, Any]
    #: miLLM's per-input ``error`` (``{code, message}``): data, not an exception, so the caller
    #: decides (``MODEL_CHANGED`` stops a run; ``TOKENIZATION_FAILED`` skips the row).
    error: dict[str, Any] | None
    latency_ms: int


def render_input(fields: Mapping[str, Any]) -> dict[str, Any]:
    """A row as one miLLM input: its chat ``messages`` as they are, or its text as one user turn."""
    messages = fields.get("messages")
    if isinstance(messages, list) and messages:
        return {"messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
    text = fields.get("text")
    if not isinstance(text, str) or not text:
        raise RowError("the row has no text or messages to score", 422)
    return {"messages": [{"role": "user", "content": text}]}


class ProbeScoreClient:
    def __init__(
        self,
        caller: EndpointCaller,
        probe_ids: Sequence[str],
        *,
        windows: Sequence[str] | None = None,
        lease_id: str | None = None,
    ) -> None:
        self.caller = caller
        self.probe_ids = list(probe_ids)
        self.windows = list(windows) if windows is not None else None
        self.lease_id = lease_id

    def request_body(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "probe_ids": list(self.probe_ids),
            "inputs": [render_input(fields)],
            "return_token_ids": True,
        }
        if self.windows is not None:
            body["windows"] = list(self.windows)
        return body

    def score(self, fields: Mapping[str, Any]) -> ProbeScore:
        body = self.request_body(fields)
        response = self.caller.call(
            "POST", SCORE_PATH, body=body, purpose="scoring", openai=True, lease_id=self.lease_id
        )
        envelope = response.body
        if not isinstance(envelope, dict) or envelope.get("success") is not True:
            raise RowError(
                f"miLLM answered the probe score without success: {envelope!r}"[:500], 502
            )
        data = envelope["data"]
        [result] = data["results"]
        error = result["error"]
        return ProbeScore(
            verdicts=list(result["verdicts"]),
            token_ids=result["token_ids"],
            n_tokens=result["n_tokens"],
            model=dict(data["model"]),
            skipped=list(data["skipped"]),
            request_body=body,
            result=dict(result),
            error=dict(error) if isinstance(error, dict) else None,
            latency_ms=response.latency_ms,
        )
