# Origin: miStudio (Onegaishimas/miStudio) backend/src/mcp_server/health_gate.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: TTL cache, single flight, never-blocking `snapshot`,
# `invalidate`, `public_reason`, `_retrieve_exception`, strictly-2xx availability. Changed: one
# product (`dataworks`) whose body lists dependencies, read from ONE cached probe; a closed gate
# names the dependency; `gated_call` replaces the decorator (010 FTID section 3.3).
"""Per-dependency availability for the MCP tools (FR-010.13, FR-010.14).

Tools stay registered through outages: an MCP client caches its tool list, and an agent must be
able to tell "the backend is down" from "this tool does not exist". A tool whose dependency is down
returns ``{"unavailable": "<dependency>", "reason": "..."}`` and issues no request.

One probe, ``GET {api_url}/api/health``, answers every dependency: ``backend`` is available on any
2xx; ``postgres``, ``redis`` and ``data_volume`` read ``body["dependencies"][dep]["ok"]`` from the
same cached body. The cache lives 10 s, so a burst of tool calls costs one probe.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

PROBE_TIMEOUT_S = 3.0
HEALTH_PATH = "/api/health"

#: The dependencies a tool may require.
DEPENDENCIES: tuple[str, ...] = ("backend", "postgres", "redis", "data_volume")


def _retrieve_exception(task: asyncio.Task[Any]) -> None:
    """Consume a background task's result without re-raising a cancellation (miStudio
    MIS-E2E-118: ``lambda t: t.exception()`` logged an ERROR on every graceful shutdown)."""
    if task.cancelled():
        return
    task.exception()


class HealthGate:
    """TTL-cached availability of the backend and the dependencies it reports."""

    def __init__(self, api_url: str, ttl_s: float = 10.0) -> None:
        self._url = api_url.rstrip("/")
        self._ttl = ttl_s
        # (checked_at_monotonic, backend_available, reason, body)
        self._cache: tuple[float, bool, str, dict[str, Any]] | None = None
        self._http: httpx.AsyncClient | None = None
        self._lock: asyncio.Lock | None = None
        self._refreshing: asyncio.Task[Any] | None = None
        #: Probe count, read by the single-flight test.
        self.probes = 0

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=False)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _fresh(self) -> bool:
        return self._cache is not None and time.monotonic() - self._cache[0] < self._ttl

    async def _refresh(self) -> tuple[float, bool, str, dict[str, Any]]:
        if self._fresh():
            assert self._cache is not None
            return self._cache
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # Re-check under the lock: a concurrent caller may have probed (single flight).
            if self._fresh():
                assert self._cache is not None
                return self._cache
            available, reason, body = await self._probe()
            # Stamped AFTER the probe, so a slow probe does not eat the TTL window.
            self._cache = (time.monotonic(), available, reason, body)
            return self._cache

    async def check(self, dependency: str) -> tuple[bool, str]:
        """``(available, reason)`` for one dependency."""
        _, backend_ok, reason, body = await self._refresh()
        return self._judge(dependency, backend_ok, reason, body)

    @staticmethod
    def _judge(
        dependency: str, backend_ok: bool, reason: str, body: dict[str, Any]
    ) -> tuple[bool, str]:
        if dependency not in DEPENDENCIES:
            return False, f"unknown dependency '{dependency}'"
        if not backend_ok:
            return False, reason
        if dependency == "backend":
            return True, "ok"
        deps = body.get("dependencies") if isinstance(body, dict) else None
        entry = deps.get(dependency) if isinstance(deps, dict) else None
        if not isinstance(entry, dict):
            return False, f"{dependency} not reported by the backend"
        if entry.get("ok") is True:
            return True, "ok"
        return False, f"{dependency} down: {entry.get('reason') or 'no reason given'}"

    async def unavailable(self, *dependencies: str) -> dict[str, str] | None:
        """The structured refusal for the first dependency that is down, else ``None``."""
        for dependency in dependencies:
            ok, reason = await self.check(dependency)
            if not ok:
                return {"unavailable": dependency, "reason": reason}
        return None

    def snapshot(self, dependency: str) -> tuple[bool | None, str]:
        """Last known state WITHOUT probing; ``(None, "not probed yet")`` before the first probe.

        The ``/health`` route answers orchestrator probes with a one-second timeout, so it must
        never wait on the backend: a hung backend degrades the tools, it does not restart the pod.
        A stale entry starts a background refresh.
        """
        pending = self._refreshing
        if not self._fresh() and (pending is None or pending.done()):
            try:
                task = asyncio.get_running_loop().create_task(self._refresh())
                task.add_done_callback(_retrieve_exception)
                self._refreshing = task
            except RuntimeError:
                pass  # no running loop (synchronous caller); the next check() probes
        if self._cache is None:
            return None, "not probed yet"
        _, backend_ok, reason, body = self._cache
        return self._judge(dependency, backend_ok, reason, body)

    def invalidate(self) -> None:
        self._cache = None

    @staticmethod
    def public_reason(reason: str) -> str:
        """A coarse category safe for the UNAUTHENTICATED ``/health`` route; detailed reasons
        carry internal URLs and exception text and stay in authenticated tool results."""
        if reason in ("ok", "not probed yet"):
            return reason
        if reason.startswith("HTTP "):
            return "error response"
        if " down: " in reason:
            return "down"
        if reason.endswith("not reported by the backend"):
            return "not reported"
        if reason.startswith("timed out"):
            return "timed out"
        if reason.startswith("unknown dependency"):
            return "unknown dependency"
        return "unreachable"

    async def _probe(self) -> tuple[bool, str, dict[str, Any]]:
        self.probes += 1
        url = f"{self._url}{HEALTH_PATH}"
        try:
            response = await self._client().get(url)
        except httpx.TimeoutException:
            return False, f"timed out after {PROBE_TIMEOUT_S}s ({url})", {}
        except httpx.HTTPError as exc:
            return False, f"{type(exc).__name__}: {exc} ({url})", {}
        except Exception as exc:  # noqa: BLE001 - the gate's contract is to answer, never raise
            # httpx.InvalidURL derives from Exception, not HTTPError (miStudio MIS-E2E-116).
            return False, f"probe failed, {type(exc).__name__}: {exc} ({url})", {}
        # Strictly 2xx, redirects not followed: a 3xx from an ingress fronting a dead backend is
        # not availability.
        if not 200 <= response.status_code < 300:
            return False, f"HTTP {response.status_code} from {url}", {}
        try:
            body = response.json()
        except ValueError:
            return False, f"HTTP {response.status_code} from {url} was not JSON", {}
        return True, "ok", body if isinstance(body, dict) else {}


async def gated_call(
    gate: HealthGate, dependencies: tuple[str, ...], call: Callable[[], Awaitable[Any]]
) -> Any:
    """Run ``call`` only when every dependency is up; otherwise return the structured refusal.

    A connection failure DURING the call (the gate said ok seconds ago) invalidates the cache and
    becomes the same structured shape, so the next call probes rather than trusting a stale "ok".
    """
    from .client import DataworksError

    down = await gate.unavailable(*dependencies)
    if down is not None:
        return down
    try:
        return await call()
    except DataworksError as exc:
        if exc.code == "BACKEND_UNREACHABLE":
            gate.invalidate()
            return {"unavailable": "backend", "reason": exc.message}
        raise
