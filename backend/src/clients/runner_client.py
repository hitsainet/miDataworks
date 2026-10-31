"""The one client of the isolated runner (feature 018; FTDD §6.2, §8.2; FTID §5.3; FTASKS 6.1).

Sync (:class:`RunnerClient`, Celery workers) and async (:class:`AsyncRunnerClient`, the API) over
``httpx``. Both share every rule below, written once:

- **The caller's text rule (FR-018.5, M4).** Every text has its lone surrogates replaced with
  U+FFFD (``runner_contract.text.replace_lone_surrogates``) BEFORE it is encoded. The runner refuses
  a text it cannot encode (``text_invalid``); it never guesses.
- **Documents by hash (FTID I10).** A request names the document by ``code_sha256``; its canonical
  bytes go only when the runner answers 409 ``document_not_cached`` (one resend), or when the
  caller asks for them up front (dry runs and saves, whose document is rarely cached).
- **Bounded retry (§8.2).** A refused connection or a 502/503/504 is retried with backoff
  (100 ms doubling to 1.6 s) while the budget lasts; then every result of the request reads
  ``rule_worker_unavailable`` and :attr:`RunAnswer.available` is false. A timeout is not retried
  (the runner may still be running the work); it is unavailable at once.
- **A named refusal is raised**, never mapped to a result: :class:`RunnerRefused` carries the
  runner's code (``limit_below_floor``, ``runner_contract_unknown``, ``code_rule_invalid``, ...).

Nothing here logs a text, a source or a parameter (FR-018.25). Standard library, ``httpx`` and the
contract package only: no settings, no database (the runner tests drive this client against the
real runner with nothing else installed).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..runner_contract.hashing import canonical_document_bytes, code_sha256
from ..runner_contract.reasons import Reason
from ..runner_contract.text import replace_lone_surrogates

CONTRACT = "midataworks.runner/v1"
#: Statuses that mean "the runner is not there right now": retried while the budget lasts.
RETRY_STATUSES = frozenset({502, 503, 504})
#: The backoff schedule (§8.2): 100 ms, doubling, capped at 1.6 s.
FIRST_DELAY_S = 0.1
MAX_DELAY_S = 1.6
MODES = ("stored", "dry_run", "save_check")
LANES = ("interactive", "batch")


def unavailable() -> dict[str, str]:
    """One result that says the runner could not run the call (a host fault, FR-018.22)."""
    return {"reason": Reason.RULE_WORKER_UNAVAILABLE.value}


class RunnerRefused(Exception):
    """The runner answered with a named refusal (4xx); ``code`` is the runner's own."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class DocumentRef:
    """A code-rule document as the runner channel carries it: its hash and its canonical text."""

    code_sha256: str
    canonical_utf8: str

    @classmethod
    def of(cls, document: Mapping[str, Any]) -> DocumentRef:
        """From a document dict (the hashed body): the one hash rule (``runner_contract``)."""
        return cls(code_sha256(dict(document)), canonical_document_bytes(dict(document)).decode())


@dataclass(frozen=True)
class RunCall:
    """One function over some texts; ``repeat`` 2 runs each text on two fresh instances."""

    function: str
    params: Mapping[str, Any]
    texts: Sequence[str]
    repeat: int = 1

    @property
    def n_results(self) -> int:
        return len(self.texts) * self.repeat


@dataclass(frozen=True)
class RunAnswer:
    """The runner's results, per call and per text (× repeat), and what it reported about itself.

    ``available`` is false when the runner could not be reached within the budget; then every
    result is ``rule_worker_unavailable`` and ``contract_version``/``bundle_digest`` are ``None``.
    """

    results: list[list[dict[str, Any]]]
    contract_version: str | None
    bundle_digest: str | None
    available: bool
    attempts: int


def _check(mode: str, lane: str, calls: Sequence[RunCall]) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    if not calls:
        raise ValueError("at least one call is required")


def run_body(
    document: DocumentRef,
    calls: Sequence[RunCall],
    *,
    mode: str,
    lane: str,
    limits: Mapping[str, int],
    include_document: bool,
) -> dict[str, Any]:
    """The ``POST /v1/run`` body (FTDD §6.2). Texts pass the caller's text rule here, once."""
    doc: dict[str, Any] = {"code_sha256": document.code_sha256}
    if include_document:
        doc["canonical_utf8"] = document.canonical_utf8
    return {
        "contract": CONTRACT,
        "mode": mode,
        "lane": lane,
        "limits": dict(limits),
        "document": doc,
        "calls": [
            {
                "function": call.function,
                "params": dict(call.params),
                "texts": [replace_lone_surrogates(text) for text in call.texts],
                "repeat": call.repeat,
            }
            for call in calls
        ],
    }


def inspect_body(document: DocumentRef) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "document": {
            "code_sha256": document.code_sha256,
            "canonical_utf8": document.canonical_utf8,
        },
    }


def _error_of(response: httpx.Response) -> tuple[str, str]:
    try:
        error = response.json()["error"]
        return str(error["code"]), str(error.get("message", ""))
    except (ValueError, KeyError, TypeError):
        return f"http_{response.status_code}", "the runner answered without an error body"


#: What one attempt's response means for the loop.
_OK, _RETRY, _RESEND = "ok", "retry", "resend"


def _classify(response: httpx.Response, include_document: bool) -> str:
    if response.status_code == 200:
        return _OK
    if response.status_code in RETRY_STATUSES:
        return _RETRY
    code, message = _error_of(response)
    if response.status_code == 409 and code == "document_not_cached" and not include_document:
        return _RESEND
    raise RunnerRefused(code, message, response.status_code)


def _answer(response: httpx.Response, calls: Sequence[RunCall], attempts: int) -> RunAnswer:
    try:
        body = response.json()
    except ValueError:  # 018 Review R2 D-R2-4: a 200 that is not JSON is a named refusal, not a 500
        raise RunnerRefused("runner_reply_invalid", "the reply is not JSON", 200) from None
    if not isinstance(body, dict):
        raise RunnerRefused("runner_reply_invalid", "the reply is not a JSON object", 200)
    results = body.get("results")
    if not isinstance(results, list) or [len(r) for r in results] != [c.n_results for c in calls]:
        raise RunnerRefused("runner_reply_invalid", "results do not match the calls sent", 200)
    return RunAnswer(
        results=results,
        contract_version=body.get("contract_version"),
        bundle_digest=body.get("bundle_digest"),
        available=True,
        attempts=attempts,
    )


def _unavailable_answer(calls: Sequence[RunCall], attempts: int) -> RunAnswer:
    return RunAnswer(
        results=[[unavailable() for _ in range(c.n_results)] for c in calls],
        contract_version=None,
        bundle_digest=None,
        available=False,
        attempts=attempts,
    )


class _Backoff:
    """The retry budget: ``next_delay()`` is the next wait, or ``None`` when the budget is spent."""

    def __init__(self, budget_s: float, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.deadline = clock() + budget_s
        self.delay = FIRST_DELAY_S

    def next_delay(self) -> float | None:
        if self.clock() + self.delay > self.deadline:
            return None
        delay, self.delay = self.delay, min(self.delay * 2, MAX_DELAY_S)
        return delay


def _retryable(exc: httpx.HTTPError) -> bool:
    """A refused or dropped connection is retried; a timeout is not (work may be running)."""
    return isinstance(exc, httpx.ConnectError | httpx.RemoteProtocolError)


class RunnerClient:
    """The synchronous client (Celery workers). ``budget_s`` bounds the retries of one request."""

    def __init__(
        self,
        base_url: str,
        *,
        budget_s: float = 10.0,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.budget_s = budget_s
        self.timeout_s = timeout_s
        self.transport = transport
        self.sleep = sleep
        self.clock = clock

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout_s, transport=self.transport)

    def run(
        self,
        document: DocumentRef,
        calls: Sequence[RunCall],
        *,
        mode: str,
        lane: str,
        limits: Mapping[str, int],
        send_document: bool = False,
    ) -> RunAnswer:
        _check(mode, lane, calls)
        include, attempts = send_document, 0
        backoff = _Backoff(self.budget_s, self.clock)
        with self._client() as http:
            while True:
                attempts += 1
                body = run_body(
                    document, calls, mode=mode, lane=lane, limits=limits, include_document=include
                )
                try:
                    response = http.post(self.base_url + "/v1/run", json=body)
                except httpx.HTTPError as exc:
                    if not _retryable(exc):
                        return _unavailable_answer(calls, attempts)
                    verdict = _RETRY
                else:
                    verdict = _classify(response, include)
                if verdict == _OK:
                    return _answer(response, calls, attempts)
                if verdict == _RESEND:
                    include = True
                    continue
                delay = backoff.next_delay()
                if delay is None:
                    return _unavailable_answer(calls, attempts)
                self.sleep(delay)

    def inspect(self, document: DocumentRef) -> dict[str, Any] | None:
        """``POST /v1/inspect``: ``{imports, functions}`` or ``{reason, ...}``; ``None`` when the
        runner could not be reached within the budget."""
        backoff = _Backoff(self.budget_s, self.clock)
        with self._client() as http:
            while True:
                try:
                    response = http.post(self.base_url + "/v1/inspect", json=inspect_body(document))
                except httpx.HTTPError as exc:
                    if not _retryable(exc):
                        return None
                else:
                    if _classify(response, True) == _OK:
                        answer: dict[str, Any] = response.json()
                        return answer
                delay = backoff.next_delay()
                if delay is None:
                    return None
                self.sleep(delay)

    def health(self) -> dict[str, Any] | None:
        """The runner's ``/health`` body (200 or 503 with a body), or ``None`` when unreachable."""
        try:
            with self._client() as http:
                response = http.get(self.base_url + "/health")
            body: dict[str, Any] = response.json()
            return body
        except (httpx.HTTPError, ValueError):
            return None


class AsyncRunnerClient:
    """The asynchronous client (the API): the same rules as :class:`RunnerClient`."""

    def __init__(
        self,
        base_url: str,
        *,
        budget_s: float = 10.0,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.budget_s = budget_s
        self.timeout_s = timeout_s
        self.transport = transport
        self.sleep = sleep
        self.clock = clock

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout_s, transport=self.transport)

    async def run(
        self,
        document: DocumentRef,
        calls: Sequence[RunCall],
        *,
        mode: str,
        lane: str,
        limits: Mapping[str, int],
        send_document: bool = False,
    ) -> RunAnswer:
        _check(mode, lane, calls)
        include, attempts = send_document, 0
        backoff = _Backoff(self.budget_s, self.clock)
        async with self._client() as http:
            while True:
                attempts += 1
                body = run_body(
                    document, calls, mode=mode, lane=lane, limits=limits, include_document=include
                )
                try:
                    response = await http.post(self.base_url + "/v1/run", json=body)
                except httpx.HTTPError as exc:
                    if not _retryable(exc):
                        return _unavailable_answer(calls, attempts)
                    verdict = _RETRY
                else:
                    verdict = _classify(response, include)
                if verdict == _OK:
                    return _answer(response, calls, attempts)
                if verdict == _RESEND:
                    include = True
                    continue
                delay = backoff.next_delay()
                if delay is None:
                    return _unavailable_answer(calls, attempts)
                await self.sleep(delay)

    async def inspect(self, document: DocumentRef) -> dict[str, Any] | None:
        backoff = _Backoff(self.budget_s, self.clock)
        async with self._client() as http:
            while True:
                try:
                    response = await http.post(
                        self.base_url + "/v1/inspect", json=inspect_body(document)
                    )
                except httpx.HTTPError as exc:
                    if not _retryable(exc):
                        return None
                else:
                    if _classify(response, True) == _OK:
                        answer: dict[str, Any] = response.json()
                        return answer
                delay = backoff.next_delay()
                if delay is None:
                    return None
                await self.sleep(delay)

    async def health(self) -> dict[str, Any] | None:
        try:
            async with self._client() as http:
                response = await http.get(self.base_url + "/health")
            body: dict[str, Any] = response.json()
            return body
        except (httpx.HTTPError, ValueError):
            return None
