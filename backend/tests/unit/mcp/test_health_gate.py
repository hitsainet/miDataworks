"""The health gate: per-dependency refusals, single flight, a non-blocking snapshot (6.5)."""

from __future__ import annotations

import asyncio

import httpx

from src.mcp_server.client import DataworksError
from src.mcp_server.health_gate import HealthGate, gated_call

HEALTHY = {
    "status": "ok",
    "dependencies": {
        "postgres": {"ok": True, "reason": None},
        "redis": {"ok": False, "reason": "ConnectionError: cannot reach Redis"},
        "data_volume": {"ok": True, "reason": None},
    },
}


def _gate(handler, ttl: float = 10.0) -> HealthGate:  # type: ignore[no-untyped-def]
    gate = HealthGate("http://backend", ttl_s=ttl)
    gate._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # noqa: SLF001
    return gate


async def test_each_dependency_is_read_from_one_probe() -> None:
    gate = _gate(lambda r: httpx.Response(200, json=HEALTHY))
    assert await gate.check("backend") == (True, "ok")
    assert await gate.check("postgres") == (True, "ok")
    ok, reason = await gate.check("redis")
    assert not ok and "cannot reach Redis" in reason
    assert gate.probes == 1


async def test_a_closed_gate_returns_the_structured_shape_without_calling() -> None:
    gate = _gate(lambda r: httpx.Response(200, json=HEALTHY))
    called: list[int] = []

    async def body() -> dict[str, bool]:
        called.append(1)
        return {"ok": True}

    result = await gated_call(gate, ("backend", "postgres", "redis"), body)
    assert result["unavailable"] == "redis" and "Redis" in result["reason"]
    assert called == []
    assert await gated_call(gate, ("backend", "postgres"), body) == {"ok": True}
    assert called == [1]


async def test_an_unreachable_backend_closes_every_dependency() -> None:
    def refuse(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    gate = _gate(refuse)
    for dep in ("backend", "postgres", "redis", "data_volume"):
        ok, _ = await gate.check(dep)
        assert not ok
    assert gate.public_reason((await gate.check("backend"))[1]) == "unreachable"


async def test_a_non_2xx_or_a_redirect_is_not_available() -> None:
    gate = _gate(lambda r: httpx.Response(302, headers={"location": "/login"}))
    ok, reason = await gate.check("backend")
    assert not ok and reason.startswith("HTTP 302")


async def test_one_probe_per_ttl_under_concurrent_calls() -> None:
    async def slow(_r: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=HEALTHY)

    gate = _gate(slow)
    await asyncio.gather(*(gate.check("postgres") for _ in range(20)))
    assert gate.probes == 1


async def test_a_hanging_probe_does_not_block_the_snapshot() -> None:
    release = asyncio.Event()

    async def hang(_r: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json=HEALTHY)

    gate = _gate(hang)
    started = asyncio.get_running_loop().time()
    assert gate.snapshot("backend") == (None, "not probed yet")
    assert asyncio.get_running_loop().time() - started < 0.05
    release.set()
    await asyncio.sleep(0.01)
    assert gate.snapshot("backend") == (True, "ok")


async def test_a_mid_call_connection_failure_invalidates_the_cache() -> None:
    gate = _gate(lambda r: httpx.Response(200, json=HEALTHY))
    await gate.check("backend")

    async def unreachable() -> None:
        raise DataworksError(0, "BACKEND_UNREACHABLE", "gone")

    result = await gated_call(gate, ("backend",), unreachable)
    assert result == {"unavailable": "backend", "reason": "gone"}
    await gate.check("backend")
    assert gate.probes == 2, "the stale 'ok' must not be trusted after a failed call"


async def test_other_backend_errors_still_raise() -> None:
    gate = _gate(lambda r: httpx.Response(200, json=HEALTHY))

    async def refused() -> None:
        raise DataworksError(409, "CONFLICT", "no")

    try:
        await gated_call(gate, ("backend",), refused)
    except DataworksError as exc:
        assert exc.code == "CONFLICT"
    else:  # pragma: no cover
        raise AssertionError("a refusal must reach the agent as an error")


def test_public_reasons_carry_no_internal_url() -> None:
    for reason in (
        "ConnectError: refused (http://midataworks-backend:8000/api/health)",
        "timed out after 3.0s (http://midataworks-backend:8000/api/health)",
        "HTTP 503 from http://midataworks-backend:8000/api/health",
        "redis down: ConnectionError: cannot reach redis://redis:6379/0",
    ):
        public = HealthGate.public_reason(reason)
        assert "http" not in public and "redis://" not in public, public
