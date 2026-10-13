"""The loopback relay: every model call from a generation stage goes through ONE tested place
(FR-003.15, FR-003.18, FR-003.26; FTDD 003 section 6.5; FTID 003 section 3.9; ADR-010, ADR-012).

SELF-CONTAINED (standard library, httpx, starlette, uvicorn): the Data Designer image copies this
file beside its runner and cannot import the rest of ``src``
(``tests/unit/test_designer_runner_imports.py``). A Starlette app on ``127.0.0.1`` with an ephemeral port, run in a daemon thread for one step. A
caller (Data Designer's OpenAI provider, or a native generator operator) is given ``base_url`` and
the per-step ``nonce`` as its API key. The relay:

- refuses any request without the nonce (401);
- forwards to the role's REAL endpoint, holding the real key in process memory only (never an
  environment variable, a file, a log or a record);
- sends ``X-miLLM-Strict: true`` always (FR-005.10), and to miLLM the lease header and
  ``X-miLLM-Load-Policy: refuse`` (ADR-012);
- on ``503``/``429`` waits ``Retry-After`` (capped), else backs off exponentially, and never turns
  a wait into a failure (FR-005.46);
- maps a context-overflow answer to a recorded skip ``context_overflow``, not retried (FR-005.47);
- merges the step's ``body_overrides`` into every forwarded body, refusing at construction a key
  the relay owns (``relay_override_conflict``, FR-003.26);
- records per request: row key, status, response model, revision, the RAW ``X-miLLM-Steering``
  header, latency, attempts and any skip reason (FR-007.34). The row key comes from the
  ``X-Dataworks-Row-Key`` header or a ``dw_row_key`` body field, stripped before forwarding.

Spike 1.4 (2026-10-07) found Data Designer 0.9.4 sends ``extra_body`` STATICALLY (a template is not
rendered per record), so a Data Designer request cannot carry its row key; native callers send the
header. See the implementation controls record.
"""

from __future__ import annotations

import logging
import secrets
import socket
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)

ROW_KEY_HEADER = "X-Dataworks-Row-Key"
ROW_KEY_FIELD = "dw_row_key"
#: Body keys the relay owns: an override naming one is refused (FR-003.26).
RELAY_OWNED_FIELDS = frozenset({"model", "messages", "prompt", "stream", ROW_KEY_FIELD})
#: FR-005.46: the longest single Retry-After wait honoured, and the backoff cap without one.
MAX_RETRY_AFTER_S = 60.0
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 30.0
MAX_ATTEMPTS = 8
OVERFLOW_MARKERS = ("context_length_exceeded", "context length", "maximum context", "too long")


class RelayError(Exception):
    """A relay refusal with a stable ``code`` (the backend maps it into its error envelope)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class Endpoint(Protocol):
    """Where the relay forwards: the backend's ``ResolvedEndpoint`` or the designer worker's own."""

    @property
    def base_url(self) -> str: ...

    @property
    def model(self) -> str | None: ...

    @property
    def api_key(self) -> str | None: ...

    @property
    def is_millm(self) -> bool: ...


class HasLeaseId(Protocol):
    @property
    def lease_id(self) -> str: ...


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def is_overflow(status: int, text: str) -> bool:
    return status in {400, 413, 422} and any(m in text.lower() for m in OVERFLOW_MARKERS)


class LoopbackRelay:
    """Context manager: ``with LoopbackRelay(endpoint) as relay: relay.base_url, relay.nonce``."""

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        body_overrides: dict[str, Any] | None = None,
        lease: HasLeaseId | None = None,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        overrides = dict(body_overrides or {})
        clash = sorted(set(overrides) & RELAY_OWNED_FIELDS)
        if clash:
            raise RelayError(
                "relay_override_conflict",
                f"body_overrides may not set {clash}: the relay sets those itself.",
                {"fields": clash},
            )
        self._endpoint = endpoint
        self._key: str | None = endpoint.api_key
        self._overrides = overrides
        self._lease = lease
        self._sleep = sleep
        self._transport = transport
        self._timeout = timeout_s
        self.nonce = secrets.token_urlsafe(32)
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    # --- lifecycle ---------------------------------------------------------------------------

    def __enter__(self) -> LoopbackRelay:
        import uvicorn

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(self.app(), log_level="warning", access_log=False, lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RelayError("worker_unavailable", "The loopback relay did not start.")
            time.sleep(0.01)
        self.base_url = f"http://127.0.0.1:{port}/v1"
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._key = None  # the real key's last reference in this object

    # --- the app -------------------------------------------------------------------------------

    def app(self) -> Any:
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        async def forward(request: Request) -> Response:
            if request.headers.get("authorization") != f"Bearer {self.nonce}":
                return JSONResponse({"error": {"message": "relay: bad credentials"}}, 401)
            body = await request.json()
            row_key = request.headers.get(ROW_KEY_HEADER)
            if isinstance(body, dict):
                field_key = body.pop(ROW_KEY_FIELD, None)
                row_key = row_key or (str(field_key) if field_key is not None else None)
                body = deep_merge(body, self._overrides)
            path = request.path_params["path"]
            import anyio.to_thread

            status, headers, content = await anyio.to_thread.run_sync(
                self.forward_sync, path, body, row_key
            )
            return Response(content, status_code=status, headers=headers)

        return Starlette(routes=[Route("/v1/{path:path}", forward, methods=["POST"])])

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-miLLM-Strict": "true"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        if self._endpoint.is_millm:
            headers["X-miLLM-Load-Policy"] = "refuse"
            if self._lease is not None:
                headers["X-miLLM-Lease"] = self._lease.lease_id
        return headers

    def forward_sync(
        self, path: str, body: Any, row_key: str | None
    ) -> tuple[int, dict[str, str], bytes]:
        """Forward one request with backoff; record it. Returns status, headers and body."""
        url = self._endpoint.base_url.rstrip("/") + "/" + path.lstrip("/")
        started = time.monotonic()
        attempts = 0
        waited = 0.0
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            while True:
                attempts += 1
                try:
                    response = client.post(url, json=body, headers=self.headers())
                except httpx.TransportError as exc:
                    if attempts >= MAX_ATTEMPTS:
                        self._record(row_key, 502, None, started, attempts, "unreachable", None)
                        return (
                            502,
                            {},
                            f'{{"error":{{"message":"unreachable: {type(exc).__name__}"}}}}'.encode(),
                        )
                    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempts - 1))
                    waited += delay
                    self._sleep(delay)
                    continue
                if response.status_code in {429, 503} and attempts < MAX_ATTEMPTS:
                    delay = self._retry_after(response, attempts)
                    waited += delay
                    self._sleep(delay)
                    continue
                break
        text = response.text
        reason = None
        if is_overflow(response.status_code, text):
            reason = "context_overflow"
        elif response.status_code >= 400:
            reason = f"http_{response.status_code}"
        model = None
        if response.status_code < 400:
            try:
                parsed = response.json()
                model = parsed.get("model") if isinstance(parsed, dict) else None
            except ValueError:
                model = None
        self._record(
            row_key,
            response.status_code,
            model,
            started,
            attempts,
            reason,
            response.headers,
            waited=waited,
        )
        passthrough = {"Content-Type": response.headers.get("content-type", "application/json")}
        return response.status_code, passthrough, response.content

    def _retry_after(self, response: httpx.Response, attempts: int) -> float:
        raw = response.headers.get("retry-after")
        try:
            if raw is not None:
                return float(max(0.0, min(MAX_RETRY_AFTER_S, float(raw))))
        except ValueError:
            pass
        return float(min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** (attempts - 1)))

    def _record(
        self,
        row_key: str | None,
        status: int,
        model: str | None,
        started: float,
        attempts: int,
        reason: str | None,
        headers: Any,
        *,
        waited: float = 0.0,
    ) -> None:
        record = {
            "row_key": row_key,
            "status": status,
            "model": model,
            "revision": headers.get("x-millm-model-revision") if headers is not None else None,
            "steering_header": headers.get("x-millm-steering") if headers is not None else None,
            # Feature 007: miLLM's seed echo (FR-25.13.6), so a seed is confirmed only when echoed.
            "seed_header": headers.get("x-millm-seed") if headers is not None else None,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "attempts": attempts,
            "waited_s": round(waited, 3),
            "reason": reason,
        }
        with self._lock:
            self.records.append(record)
