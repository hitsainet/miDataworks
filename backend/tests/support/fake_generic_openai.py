"""A generic OpenAI-compatible server that is NOT miLLM (the standalone audit, 2026-10-07).

Written independently of ``fake_millm`` on purpose: a stand-in built by editing the miLLM fake
would keep whatever miLLM-ness the edit forgot, and the standalone guard would then agree with the
code by construction. This one is shaped like vLLM or llama.cpp's server:

- ``GET /v1/models``; ``POST /v1/completions`` (scoring mode: ``top_logprobs`` keyed
  ``token_id:<id>`` when ``logprobs`` is asked for); ``POST /v1/chat/completions`` (a judge
  verdict, or generated text when ``generation_mode``); ``POST /v1/embeddings``;
- **every ``/api/...`` route answers ``404``** — there is no miLLM management surface, no health
  detail, no lease, no probe, no steering profile;
- **no ``X-miLLM-*`` response header is ever sent**, and the request headers miDataworks adds
  (``X-miLLM-Strict`` and friends) are ignored, as a real non-miLLM server ignores them;
- ``/v1/batches`` and ``/v1/files`` are not served (``404``).

Every request is recorded, so a test can assert that nothing miLLM-only was asked of it.
``handle`` takes an ``httpx.Request``; serve it over loopback with
``generation_fixtures.FakeMillmServer(fake)`` or in process with ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import httpx

MODEL = "generic-chat-7b"
JUDGE_MODEL = "generic-judge-3b"
EMBED_MODEL = "generic-embed"
TRUE_ID, FALSE_ID = 1802, 3721


def _p(text: str) -> float:
    return 0.02 + 0.96 * (hashlib.sha256(text.encode()).digest()[0] / 255.0)


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeGenericOpenAI:
    models: tuple[str, ...] = (MODEL, JUDGE_MODEL, EMBED_MODEL)
    generation_mode: bool = False
    verdict: str = "Looks fine.\nVERDICT: yes"
    requests: list[Seen] = field(default_factory=list)

    def calls(self, path: str) -> list[Seen]:
        return [r for r in self.requests if r.path == path]

    def miLLM_only_calls(self) -> list[Seen]:
        """Requests a non-miLLM server should never have been asked (management, batch, lease)."""
        return [
            r
            for r in self.requests
            if r.path.startswith("/api/")
            and r.path not in {"/api/health/detailed", "/api/models"}
            or r.path.startswith("/v1/batches")
            or r.path.startswith("/v1/files")
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handle(request))

    @staticmethod
    def _json(status: int, body: Any) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        try:
            body = json.loads(request.content) if request.content else None
        except ValueError:
            body = None
        self.requests.append(Seen(request.method, path, dict(request.headers), body))
        if path == "/v1/models" and request.method == "GET":
            return self._json(
                200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]}
            )
        if path == "/v1/completions" and request.method == "POST":
            return self._completions(body or {})
        if path == "/v1/chat/completions" and request.method == "POST":
            return self._chat(body or {})
        if path == "/v1/embeddings" and request.method == "POST":
            return self._embeddings(body or {})
        return self._json(404, {"detail": "Not Found"})

    def _unknown_model(self, body: dict[str, Any]) -> httpx.Response | None:
        if body.get("model") not in self.models:
            return self._json(
                404,
                {"error": {"message": f"model {body.get('model')!r} not found", "code": None}},
            )
        return None

    def _completions(self, body: dict[str, Any]) -> httpx.Response:
        refused = self._unknown_model(body)
        if refused is not None:
            return refused
        prompt = str(body.get("prompt", ""))
        p = _p(prompt)
        choice: dict[str, Any] = {"index": 0, "text": "true", "finish_reason": "length"}
        if body.get("logprobs"):
            top = {f"token_id:{FALSE_ID}": math.log(1 - p), f"token_id:{TRUE_ID}": math.log(p)}
            choice["logprobs"] = {"top_logprobs": [top], "tokens": [f"token_id:{TRUE_ID}"]}
        return self._json(
            200,
            {
                "id": "cmpl-generic",
                "object": "text_completion",
                "model": body["model"],
                "choices": [choice],
                "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": 1},
            },
        )

    def _chat(self, body: dict[str, Any]) -> httpx.Response:
        refused = self._unknown_model(body)
        if refused is not None:
            return refused
        messages = body.get("messages") or []
        text = " ".join(str(m.get("content", "")) for m in messages)
        if body.get("logprobs") is True:
            p = _p(text)
            choice: dict[str, Any] = {
                "index": 0,
                "message": {"role": "assistant", "content": "true"},
                "logprobs": {
                    "content": [
                        {
                            "token": f"token_id:{TRUE_ID}",
                            "logprob": math.log(p),
                            "top_logprobs": [
                                {"token": f"token_id:{FALSE_ID}", "logprob": math.log(1 - p)},
                                {"token": f"token_id:{TRUE_ID}", "logprob": math.log(p)},
                            ],
                        }
                    ]
                },
            }
        else:
            last = str(messages[-1].get("content", "")) if messages else ""
            answer = f"A reply about [{last[:60]}]" if self.generation_mode else self.verdict
            choice = {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        return self._json(
            200,
            {
                "id": "chat-generic",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [choice],
                "usage": {"prompt_tokens": max(1, len(text) // 4), "completion_tokens": 8},
            },
        )

    def _embeddings(self, body: dict[str, Any]) -> httpx.Response:
        refused = self._unknown_model(body)
        if refused is not None:
            return refused
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = []
        for i, item in enumerate(items):
            digest = hashlib.sha256(str(item).encode()).digest()
            data.append(
                {"object": "embedding", "index": i, "embedding": [b / 255.0 for b in digest[:16]]}
            )
        return self._json(200, {"object": "list", "model": body["model"], "data": data})
