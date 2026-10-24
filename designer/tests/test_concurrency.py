"""007 FTASKS 1.7: Data Designer sends ONE request at a time through the relay (FTDD 007 §9).

The runner's ``ModelConfig`` sets ``max_parallel_requests=1`` (Data Designer's own default is 4).
A fake OpenAI-compatible server that holds every request open for a moment counts how many are in
flight at once while a real Data Designer generation of 24 records runs through the loopback relay,
the path production takes (``runner.generate``).

The positive control removes the setting (the library default) through the same counter and
requires an overlap above one: without it a counter that could never see concurrency would pass the
first test vacuously.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import dd_paths
from src.operators.data_designer import runner

CATALOGUE = json.loads(dd_paths.CATALOGUE.read_text())
ROWS = 24
HOLD_S = 0.15


class OverlapServer:
    """Answers chat completions after ``HOLD_S`` seconds; records the most requests in flight."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.count = 0
        self.spans: list[tuple[float, float]] = []
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self.base_url = ""

    async def chat(self, request: Request) -> JSONResponse:
        body = await request.json()
        start = time.monotonic()
        with self._lock:
            self.in_flight += 1
            self.count += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(HOLD_S)
        finally:
            with self._lock:
                self.in_flight -= 1
                self.spans.append((start, time.monotonic()))
        return JSONResponse(
            {
                "id": "c1",
                "object": "chat.completion",
                "model": body.get("model", "m"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
            }
        )

    def __enter__(self) -> OverlapServer:
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


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def _payload(base_url: str) -> dict[str, Any]:
    entry = next(o for o in CATALOGUE["operators"] if o["manifest"]["name"] == "dd_llm_text")
    return {
        "operator": "dd_llm_text@x",
        "column_class": entry["column_class"],
        "column_fields": entry["column_fields"],
        "params": {"prompt": "Rewrite: {{ text }}"},
        "params_schema": entry["manifest"]["params_schema"],
        "output_column": "llm_text",
        "seed_columns": ["text"],
        "endpoint": {"base_url": base_url, "model": "m1", "is_millm": False, "key_origin": None},
        "key_ref": None,
        "lease_id": None,
        "body_overrides": None,
    }


def _measure() -> tuple[OverlapServer, list[dict[str, Any]], list[dict[str, Any]]]:
    table = pa.table(
        {
            "text": [f"row number {i}" for i in range(ROWS)],
            "_dw_row_key": [f"{i:064x}" for i in range(ROWS)],
            "_dw_occurrence": pa.array([0] * ROWS, pa.int32()),
        }
    )
    with OverlapServer() as upstream:
        produced, records = runner.generate(table, _payload(upstream.base_url), None)
    return upstream, produced, records


def test_one_request_in_flight_through_the_relay() -> None:
    upstream, produced, records = _measure()
    print(f"\nsetting 1: {upstream.count} requests, max in flight {upstream.max_in_flight}")
    assert upstream.count == ROWS and len(records) == ROWS and len(produced) == ROWS
    assert upstream.max_in_flight == 1


def test_positive_control_the_library_default_overlaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the runner's setting the same counter sees requests overlap (default 4)."""
    import data_designer.config as c

    real = c.ChatCompletionInferenceParams

    def library_default(**kwargs: Any) -> Any:
        kwargs.pop("max_parallel_requests", None)
        return real(**kwargs)

    monkeypatch.setattr(c, "ChatCompletionInferenceParams", library_default)
    upstream, produced, _ = _measure()
    print(f"\nlibrary default: {upstream.count} requests, max in flight {upstream.max_in_flight}")
    assert upstream.count == ROWS and len(produced) == ROWS
    assert upstream.max_in_flight > 1
