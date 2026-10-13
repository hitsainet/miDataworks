"""The one outbound HTTP wrapper for endpoint roles (FR-005.10, FR-005.46 – FR-005.48; FTDD 005 6.2).

Etiquette lives here and nowhere else (an AST test forbids ``httpx`` in every other labeling
module):

- ``X-miLLM-Strict: true`` on every request to an OpenAI-compatible endpoint (miLLM FR-25.2);
- ``X-miLLM-Load-Policy: refuse`` on every scoring, judge and generation request (miLLM FR-29.4),
  so a label run can never make miLLM load or swap a model;
- ``X-miLLM-Lease`` when the caller holds a lease ticket (miLLM FR-29.3);
- the bearer key when the role has one.

Status mapping (each row is a test in ``test_endpoint_caller.py``):

| Response                                   | Outcome                                          |
|--------------------------------------------|--------------------------------------------------|
| 503                                        | wait ``Retry-After`` (or 1, 2, 4 … capped), retry |
| 409 ``model_not_resident``                 | :class:`ModelNotResident`                        |
| 409 ``model_leased``                       | :class:`LeaseLost`                               |
| 400 ``unused_fields_refused``              | :class:`StrictRefusal` (a defect, never retried) |
| 400/413/422 context-length error           | :class:`ContextOverflow` (one request, skipped)  |
| 401 / 403                                  | :class:`EndpointUnauthorized`                    |
| other 4xx                                  | :class:`RowError`                                |
| other 5xx, connect or read error           | retried, then :class:`TransientError`            |

The context-length signatures are miLLM's ``context_length_exceeded`` (``millm/core/errors.py``
``ContextLengthExceededError``, OpenAI's own code) and TEI's input-validation refusal under
``truncate: false``. **The TEI body shape is unverified** (no TEI server existed when this was
written; FTASKS 1.6): it is matched on TEI's documented ``error_type: "Validation"`` with a token
count in the message, never on a guessed exact string.

Waiting is cooperative: ``on_wait(seconds, reason)`` is called before every backpressure sleep and
may raise (cancellation, a deadline). Sleeping goes through ``sleep``, so tests use a fake clock.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from ..core.logging import register_secret
from .endpoint_errors import (
    ContextOverflow,
    EndpointNotJson,
    EndpointUnauthorized,
    EndpointUnreachable,
    LeaseLost,
    ModelNotResident,
    RowError,
    StrictRefusal,
    TransientError,
)

logger = logging.getLogger(__name__)

STRICT_HEADER = "X-miLLM-Strict"
LOAD_POLICY_HEADER = "X-miLLM-Load-Policy"
LEASE_HEADER = "X-miLLM-Lease"
STEERING_HEADER = "X-miLLM-Steering"
SEED_HEADER = "X-miLLM-Seed"

#: Purposes that do model work and therefore must never load a model (ADR-012).
MODEL_WORK: frozenset[str] = frozenset({"scoring", "judge", "generation"})

Purpose = Literal["scoring", "judge", "generation", "probe", "management"]

TransportFactory = Callable[[str], httpx.BaseTransport]

#: Tests install an ``httpx.MockTransport`` factory here (one transport per base URL).
_transport_factory: TransportFactory | None = None

_CONTEXT_CODES = frozenset({"context_length_exceeded"})
_TEI_TOKENS = re.compile(r"\btokens?\b", re.IGNORECASE)


def install_transport(factory: TransportFactory | None) -> TransportFactory | None:
    """Route every caller built afterwards through ``factory`` (tests). Returns the previous one."""
    global _transport_factory
    previous, _transport_factory = _transport_factory, factory
    return previous


def server_origin(base_url: str) -> str:
    """The server root: the base URL without a trailing ``/v1``."""
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


@dataclass(frozen=True)
class CallResponse:
    status: int
    body: Any
    headers: dict[str, str]
    latency_ms: int

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def backoff_seconds(attempt: int, cap: float) -> float:
    """1, 2, 4 … seconds for the ``attempt``-th wait (0-based), never above ``cap``."""
    return float(min(cap, 2.0 ** max(0, attempt)))


def parse_retry_after(value: str | None) -> float | None:
    """Whole or fractional seconds; anything else (an HTTP date) is ignored, never guessed."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _error_fields(body: Any) -> tuple[str | None, str, dict[str, Any]]:
    """(code, message, error object) from an OpenAI, miLLM-management or TEI error body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code")
            return (str(code).lower() if code else None), str(err.get("message") or ""), err
        if isinstance(err, str):  # TEI: {"error": "...", "error_type": "Validation"}
            return None, err, body
    return None, "", {}


def _is_context_overflow(status: int, code: str | None, message: str, err: dict[str, Any]) -> bool:
    if status not in (400, 413, 422):
        return False
    if code in _CONTEXT_CODES:
        return True
    # TEI with truncate=false (unverified shape, see the module docstring).
    return err.get("error_type") == "Validation" and bool(_TEI_TOKENS.search(message))


@dataclass
class EndpointCaller:
    """Calls one server. Hold one per run: the ``httpx.Client`` keeps its connection alive."""

    base_url: str
    api_key: str | None = field(default=None, repr=False)
    timeout_s: float = 120.0
    backoff_cap_s: float = 60.0
    transient_retries: int = 3
    sleep: Callable[[float], None] = time.sleep
    on_wait: Callable[[float, str], None] | None = None
    _client: httpx.Client | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        register_secret(self.api_key)
        self.origin = server_origin(self.base_url)

    # --- lifecycle --------------------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is None:
            transport = _transport_factory(self.origin) if _transport_factory else None
            self._client = httpx.Client(timeout=self.timeout_s, transport=transport)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> EndpointCaller:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # --- headers ----------------------------------------------------------------------------

    def headers_for(
        self, purpose: Purpose, *, openai: bool, lease_id: str | None
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if openai:
            headers[STRICT_HEADER] = "true"
        if purpose in MODEL_WORK:
            headers[LOAD_POLICY_HEADER] = "refuse"
        if lease_id is not None:
            register_secret(lease_id)
            headers[LEASE_HEADER] = lease_id
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # --- one request ------------------------------------------------------------------------

    def _send(
        self, method: str, path: str, body: Any, headers: dict[str, str]
    ) -> tuple[httpx.Response, int]:
        url = self.origin + path
        started = time.perf_counter()
        try:
            response = self._http().request(method, url, json=body, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise EndpointUnreachable(f"Could not reach {url} ({type(exc).__name__}).") from None
        except httpx.HTTPError as exc:
            raise TransientError(f"{url} failed mid-request ({type(exc).__name__}).") from None
        return response, int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _json(response: httpx.Response, url: str) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "unknown")
            raise EndpointNotJson(
                f"{url} answered with {content_type}, not JSON. The address may point at a web "
                "page rather than the model server."
            ) from None

    def upload(
        self, path: str, *, filename: str, content: bytes, fields: dict[str, str]
    ) -> CallResponse:
        """A multipart upload (miLLM ``POST /v1/files``), with the strict header."""
        headers = self.headers_for("management", openai=True, lease_id=None)
        url = self.origin + path
        started = time.perf_counter()
        try:
            response = self._http().post(
                url,
                files={"file": (filename, content, "application/jsonl")},
                data=fields,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise EndpointUnreachable(f"Could not reach {url} ({type(exc).__name__}).") from None
        latency = int((time.perf_counter() - started) * 1000)
        return CallResponse(
            response.status_code,
            self._json(response, url),
            {k.lower(): v for k, v in response.headers.items()},
            latency,
        )

    def raw_bytes(self, method: str, path: str) -> tuple[int, bytes]:
        """A request whose answer is not JSON (miLLM's ``application/jsonl`` results)."""
        headers = self.headers_for("management", openai=True, lease_id=None)
        response, _ = self._send(method, path, None, headers)
        return response.status_code, response.content

    def raw(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        purpose: Purpose = "management",
        openai: bool = False,
        lease_id: str | None = None,
    ) -> CallResponse:
        """One request with the etiquette headers, NO status mapping (the lease client and the
        probe read statuses themselves)."""
        headers = self.headers_for(purpose, openai=openai, lease_id=lease_id)
        response, latency = self._send(method, path, body, headers)
        parsed = self._json(response, self.origin + path) if response.status_code != 204 else None
        return CallResponse(
            response.status_code,
            parsed,
            {k.lower(): v for k, v in response.headers.items()},
            latency,
        )

    # --- the mapped call --------------------------------------------------------------------

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        purpose: Purpose,
        openai: bool,
        lease_id: str | None = None,
    ) -> CallResponse:
        """A request mapped per the module table. Backpressure waits and retries here; a
        transient error retries ``transient_retries`` times; every other outcome raises."""
        blind_waits = 0  # waits without a Retry-After: they walk the 1, 2, 4 ... ladder
        transient = 0
        while True:
            try:
                response = self.raw(
                    method, path, body=body, purpose=purpose, openai=openai, lease_id=lease_id
                )
            except (EndpointUnreachable, TransientError):
                if transient >= self.transient_retries:
                    raise
                self.sleep(backoff_seconds(transient, self.backoff_cap_s))
                transient += 1
                continue
            status = response.status
            if 200 <= status < 300:
                return response
            code, message, err = _error_fields(response.body)
            if status == 503:
                retry_after = parse_retry_after(response.header("Retry-After"))
                if retry_after is not None:
                    wait = retry_after
                else:
                    wait = backoff_seconds(blind_waits, self.backoff_cap_s)
                    blind_waits += 1
                reason = message or "the server is busy"
                logger.info(
                    "label_run.backpressure path=%s wait_s=%.1f retry_after=%s",
                    path,
                    wait,
                    retry_after,
                )
                if self.on_wait is not None:
                    self.on_wait(wait, reason)
                self.sleep(wait)
                continue
            if status == 409 and code == "model_not_resident":
                raise ModelNotResident(message or "The model is not loaded.", None, None)
            if status == 409 and code == "model_leased":
                raise LeaseLost(message or "Another holder leases the model.", "model_leased")
            if status == 400 and code == "unused_fields_refused":
                raise StrictRefusal(
                    "miLLM refused a field miDataworks sends; this is a defect in miDataworks. "
                    + message
                )
            if _is_context_overflow(status, code, message, err):
                raise ContextOverflow(message or "The row exceeds the model's context window.")
            if status in (401, 403):
                raise EndpointUnauthorized(
                    f"{self.origin} refused the API key ({status}). Check the key for this role."
                )
            if 400 <= status < 500:
                details = err.get("details") if isinstance(err.get("details"), dict) else None
                raise RowError(
                    f"{status} {code or ''}: {message}".strip(),
                    status,
                    server_code=code,
                    details=details,
                )
            if transient >= self.transient_retries:
                raise TransientError(f"{self.origin}{path} answered {status}: {message}")
            self.sleep(backoff_seconds(transient, self.backoff_cap_s))
            transient += 1
