"""``GET /api/health``: each dependency's state, reported and never raised (ADR-013; task 3.7).

The route answers 200 while the API process is up. A dependency that is down is a ``false`` with
a reason, not an exception: miStudio's proxy (BRD-MIS-DATAWORKS-001) reads any 2xx as "the API is
available" and the body as "what works". Shape (010 FTDD section 5.6):

    {"status": "ok" | "degraded",
     "dependencies": {"postgres": {"ok": bool, "reason": str | null}, ...},
     "resources": {...}}

miLLM and miStudio are probed at their own health routes when a base URL is configured;
otherwise they report ``configured: false`` and do not count against the status.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Any

import httpx
import psutil
import redis.asyncio as aioredis
from sqlalchemy import text

from ..core.config import get_settings
from ..core.database import get_async_engine


def _dep(ok: bool, reason: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "reason": reason, **extra}


async def check_postgres() -> dict[str, Any]:
    try:
        async with get_async_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return _dep(True)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return _dep(False, f"{type(exc).__name__}: cannot query the database")


async def check_redis() -> dict[str, Any]:
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        await client.ping()
        return _dep(True)
    except Exception as exc:  # noqa: BLE001
        return _dep(False, f"{type(exc).__name__}: cannot reach Redis")
    finally:
        await client.aclose()


def check_data_volume() -> dict[str, Any]:
    root = get_settings().data_dir
    probe = root / "tmp" / f".health-{uuid.uuid4().hex}"
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"ok")
        probe.unlink()
        return _dep(True, path=str(root))
    except OSError as exc:
        return _dep(False, f"{type(exc).__name__}: {root} is not writable", path=str(root))


async def check_http(base_url: str | None, health_path: str) -> dict[str, Any]:
    if not base_url:
        return _dep(False, "not configured", configured=False)
    url = base_url.rstrip("/") + health_path
    try:
        async with httpx.AsyncClient(timeout=get_settings().health_probe_timeout_seconds) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return _dep(False, f"{type(exc).__name__}: unreachable", configured=True, url=url)
    if not response.is_success:
        return _dep(False, f"answered {response.status_code}", configured=True, url=url)
    return _dep(True, configured=True, url=url)


def resources() -> dict[str, Any]:
    """The app's own CPU, memory and disk for the top bar. miDataworks has no GPU."""
    root = get_settings().data_dir
    disk_path = root if root.exists() else root.anchor or "/"
    disk = psutil.disk_usage(os.fspath(disk_path))
    memory = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_used_bytes": memory.used,
        "memory_total_bytes": memory.total,
        "disk_used_bytes": disk.used,
        "disk_total_bytes": disk.total,
        "disk_path": os.fspath(disk_path),
    }


# --- feature 003: operators (FTASKS 12.4) ---------------------------------------------------

_dj_cache: dict[str, Any] = {}


def consumed_queues() -> set[str] | None:
    """Queues some live worker consumes, from Celery's ``inspect`` (2 s); None if none answer."""
    from ..core.celery_app import celery_app

    answer = celery_app.control.inspect(timeout=2.0).active_queues()
    if not answer:
        return None
    return {q["name"] for queues in answer.values() for q in queues}


def check_datajuicer_worker() -> dict[str, Any]:
    """Whether a worker consumes ``datajuicer`` (cached ``DATAJUICER_HEALTH_CACHE_S``). Steps on
    that queue wait, rather than fail, while it is down (FPRD 003 section 2.3)."""
    ttl = get_settings().datajuicer_health_cache_s
    cached = _dj_cache.get("value")
    if cached is not None and time.monotonic() - _dj_cache["at"] < ttl:
        return dict(cached)
    try:
        queues = consumed_queues()
    except Exception as exc:  # noqa: BLE001 - health never raises
        queues, reason = None, f"Celery did not answer: {type(exc).__name__}"
    else:
        reason = "no worker consumes the datajuicer queue; Data-Juicer steps wait until one does"
    if queues is not None and "datajuicer" in queues:
        value = _dep(True, preview_queue="datajuicer_preview" in queues)
    else:
        value = _dep(False, reason)
    _dj_cache.update(value=value, at=time.monotonic())
    return dict(value)


def operator_registry_summary() -> dict[str, Any]:
    from ..operators.registry import current

    try:
        return {"ok": True, "states": current().summary()}
    except Exception as exc:  # noqa: BLE001 - health never raises
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:300]}


CURATION_SWEEPER_KEY = "midataworks:curation:sweeper_last_run"


async def curation_sweeper() -> dict[str, Any]:
    """When feature 004's audit sweeper last ran (FTDD 004 section 11.3); null until it has."""
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        raw = await client.get(CURATION_SWEEPER_KEY)
        value = raw.decode() if isinstance(raw, bytes) else raw
        return {"ok": True, "last_run": value}
    except Exception as exc:  # noqa: BLE001 - health never raises
        return {"ok": False, "reason": f"{type(exc).__name__}: cannot read the sweeper's last run"}
    finally:
        await client.aclose()


async def health() -> dict[str, Any]:
    settings = get_settings()
    postgres, redis_state, millm, mistudio = await asyncio.gather(
        check_postgres(),
        check_redis(),
        check_http(settings.millm_base_url, "/api/health"),
        check_http(settings.mistudio_base_url, "/api/health"),
    )
    datajuicer = await asyncio.to_thread(check_datajuicer_worker)
    dependencies = {
        "postgres": postgres,
        "redis": redis_state,
        "data_volume": check_data_volume(),
        "millm": millm,
        "mistudio": mistudio,
        "datajuicer_worker": datajuicer,
    }
    required = ("postgres", "redis", "data_volume")
    status = "ok" if all(dependencies[name]["ok"] for name in required) else "degraded"
    return {
        "status": status,
        "dependencies": dependencies,
        "resources": resources(),
        "operators": await asyncio.to_thread(operator_registry_summary),
        "curation_sweeper": await curation_sweeper(),
    }
