"""A fake of TypeSafe's JEV API (FR-005.56; D58), shaped from its documentation read 2026-10-18:
``https://docs.typesafe.ai/api.md`` (``POST /v1/systemone``), ``/primitives/choice.md`` (the choice
answer), ``/models.md`` (``GET /v1/models``), ``/sdk/python/api/exceptions.md`` (statuses; the
``x-typesafe-request-id`` header). The error BODY shape is not documented; the fake sends a plain
``{"error": {"message"}}``, and the client reads only the status."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

ORIGIN = "https://api.typesafe.ai"
KEY = "ts-test-key-0123456789abcdef"


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeTypeSafe:
    version: str = "jev-1.13.0"
    #: Responses served before the normal answer, in order: (status, headers, body).
    script: list[tuple[int, dict[str, str], Any]] = field(default_factory=list)
    #: After this many answered questions the served version becomes ``version_after``.
    switch_after: int | None = None
    version_after: str = "jev-1.14.0"
    requests: list[Recorded] = field(default_factory=list)
    answered: int = 0
    #: 017 FR-017.41: after this many answered questions every scoring call fails (HTTP 500).
    fail_after: int | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handle(request))

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append(
            Recorded(request.method, request.url.path, dict(request.headers), body)
        )
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": {"message": "Invalid API key"}})
        if request.url.path == "/v1/models" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "jev-latest",
                            "description": "stable",
                            "release_date": "2026-09-01",
                        },
                        {
                            "name": "jev-preview",
                            "description": "preview",
                            "release_date": "2026-10-01",
                        },
                    ]
                },
            )
        if request.url.path != "/v1/systemone" or request.method != "POST":
            return httpx.Response(404, json={"error": {"message": "Not found"}})
        if self.script:
            status, headers, payload = self.script.pop(0)
            return httpx.Response(status, headers=headers, json=payload)
        if self.fail_after is not None and self.answered >= self.fail_after:
            return httpx.Response(500, json={"error": {"message": "upstream unavailable"}})
        self.answered += 1
        version = (
            self.version_after
            if self.switch_after is not None and self.answered > self.switch_after
            else self.version
        )
        answers = {}
        for qid, q in body["questions"].items():
            labels = list(q["criteria"])
            funny = "joke" in str(body["state"]).lower()
            p = 0.8 if funny else 0.1
            probabilities = {labels[0]: p, labels[1]: round(1 - p, 10)}
            answers[qid] = {
                "type": "choice",
                "choice": labels[0] if p >= 0.5 else labels[1],
                "confidence": 0.6,
                "probabilities": probabilities,
            }
        return httpx.Response(
            200,
            headers={"x-typesafe-request-id": f"req-{self.answered}"},
            json={
                "model": version,
                "answers": answers,
                "usage": {"input_tokens": 100 + len(str(body["state"])), "output_tokens": 34},
            },
        )
