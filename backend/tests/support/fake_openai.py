"""A fake OpenAI-compatible server on loopback that records every request (FTID 003 section 8).

Behaviour by message content: ``OVERFLOW`` answers a context-length error; ``BUSY`` answers 503
with ``Retry-After: 0`` on the first attempt; anything else is echoed back as the completion,
with ``X-miLLM-Steering`` and ``X-miLLM-Model-Revision`` response headers.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


class FakeOpenAI:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.attempts: Counter[str] = Counter()
        self.base_url = ""
        self._server: uvicorn.Server | None = None

    @staticmethod
    def _text(body: dict[str, Any]) -> str:
        content = body.get("messages", [{}])[-1].get("content", "")
        if isinstance(content, list):
            return " ".join(part.get("text", "") for part in content)
        return str(content)

    async def chat(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.requests.append(
            {"headers": dict(request.headers), "body": body, "path": request.url.path}
        )
        text = self._text(body)
        self.attempts[text] += 1
        if "OVERFLOW" in text:
            return JSONResponse(
                {
                    "error": {
                        "message": "maximum context length exceeded",
                        "code": "context_length_exceeded",
                    }
                },
                status_code=400,
            )
        if "BUSY" in text and self.attempts[text] == 1:
            return JSONResponse(
                {"error": {"message": "queue full"}}, 503, headers={"Retry-After": "0"}
            )
        return JSONResponse(
            {
                "id": "c1",
                "object": "chat.completion",
                "model": body.get("model", "m"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": f"echo: {text}"}}
                ],
            },
            headers={
                "X-miLLM-Steering": "profile=p1;strength=0.4",
                "X-miLLM-Model-Revision": "rev-7",
            },
        )

    def __enter__(self) -> FakeOpenAI:
        app = Starlette(routes=[Route("/v1/chat/completions", self.chat, methods=["POST"])])
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off"))
        threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True).start()
        while not self._server.started:
            time.sleep(0.01)
        self.base_url = f"http://127.0.0.1:{port}/v1"
        return self

    def __exit__(self, *exc: Any) -> None:
        assert self._server is not None
        self._server.should_exit = True


@pytest.fixture
def fake_openai() -> Iterator[FakeOpenAI]:
    with FakeOpenAI() as server:
        yield server
