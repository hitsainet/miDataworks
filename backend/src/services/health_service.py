"""``GET /api/health``: each dependency's state, reported and never raised (ADR-013; task 3.7).

The route answers 200 while the API process is up. A dependency that is down is a ``false`` with
a reason, not an exception: miWorkbench's proxy (BRD-MIS-DATAWORKS-001) reads any 2xx as "the API is
available" and the body as "what works". Shape (010 FTDD section 5.6):

    {"status": "ok" | "degraded",
     "dependencies": {"postgres": {"ok": bool, "reason": str | null}, ...},
     "resources": {...}}

miServer and miWorkbench are probed at their own health routes when a base URL is configured;
otherwise they report ``configured: false`` and do not count against the status.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import psutil
import redis.asyncio as aioredis
from sqlalchemy import text

from ..core import env_names, legacy_names, runtime_settings
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


async def check_runner(base_url: str | None) -> dict[str, Any]:
    """Feature 018 (FTDD §8.4; FTID I3): the isolated runner, reported and never required.

    Reads the runner's own ``/health`` body, which answers 503 WITH a body on ``bundle_mismatch``,
    so the reason is the runner's status, not just "answered 503" (Discrepancy X6). Unset or down,
    the app stays ``ok``: declarative rules and everything else still work (FR-018.22).
    """
    if not base_url:
        return _dep(False, "not configured", configured=False)
    url = base_url.rstrip("/") + "/health"
    try:
        async with httpx.AsyncClient(timeout=get_settings().health_probe_timeout_seconds) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        return _dep(False, f"{type(exc).__name__}: unreachable", configured=True, url=url)
    try:
        body = response.json()
        status = body["status"]
    except (ValueError, KeyError, TypeError):
        return _dep(False, f"answered {response.status_code}", configured=True, url=url)
    facts = {
        "contract_version": body.get("contract_version"),
        "bundle_digest": body.get("bundle_digest"),
    }
    if status != "ok" or not response.is_success:
        return _dep(False, str(status), configured=True, url=url, **facts)
    return _dep(True, configured=True, url=url, **facts)


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


# --- feature 020: old-name evidence and where each sibling address came from -------------------

#: The Settings field holding each sibling's current address, keyed by its frozen health key
#: (``dependencies.millm`` / ``dependencies.mistudio`` stay: register DR-36, PD-10).
SIBLING_FIELDS: dict[str, str] = {"millm": "millm_base_url", "mistudio": "mistudio_base_url"}


def sibling_facts(
    settings: Any,
    overrides: dict[str, str],
    report: env_names.EnvReport,
    roles: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per sibling: ``address_source`` (``override`` | ``unset`` | ``env_legacy`` | ``env``, in that
    order of rules), ``previous_origins`` (sorted), and for miServer ``roles_on_previous_address``
    (FTID §3.5, FR-020.6, FR-020.14). Pure: the caller reads the inputs."""
    previous = {
        "millm": sorted(settings.miserver_previous_base_urls),
        "mistudio": sorted(settings.miworkbench_previous_base_urls),
    }
    facts: dict[str, dict[str, Any]] = {}
    for key, field in SIBLING_FIELDS.items():
        if field in overrides:
            source = "override"
        elif not getattr(settings, field):
            source = "unset"
        elif report.read.get(field) == env_names.old_name_for(field):
            source = "env_legacy"
        else:
            source = "env"
        facts[key] = {"address_source": source, "previous_origins": previous[key]}
    facts["millm"]["roles_on_previous_address"] = roles
    return facts


async def _roles_on_previous_address(db: Any) -> list[dict[str, Any]]:
    """Endpoint roles whose own base URL is on a previous miServer origin (FR-020.14)."""
    from sqlalchemy import select

    from ..core.sibling_origins import Sibling, current_address_for, previous_sibling_of
    from ..models.endpoint_role import EndpointRole

    rows = (await db.execute(select(EndpointRole.role, EndpointRole.base_url))).all()
    flagged = []
    for role, base_url in sorted(rows):
        if base_url and previous_sibling_of(base_url) is Sibling.MISERVER:
            flagged.append(
                {
                    "role": role,
                    "base_url": base_url,
                    "current_address": current_address_for(base_url, Sibling.MISERVER),
                }
            )
    return flagged


#: The clock the old-name evidence's one budget is measured on (a test seam).
_monotonic: Callable[[], float] = time.monotonic


async def _within(awaitable: Any, seconds: float) -> Any:
    """``asyncio.wait_for`` (a test seam: the bounded waits are recorded there)."""
    return await asyncio.wait_for(awaitable, timeout=seconds)


async def legacy_evidence() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """T2 (touch first, so this process's own reads are fresh), then the block and the roles.
    Never raises: a timeout or a failure becomes ``meta.error``.

    The touch and the read share ONE budget, the probe bound T (review round 2: each had its own,
    so a locked store held health for 2T): the read gets what the touch left, and when the touch
    used it all the table is not read and the block says why. A touch left behind finishes on its
    own; its engine has its own connect and statement timeouts."""
    bound = get_settings().health_probe_timeout_seconds
    deadline = _monotonic() + bound
    try:
        await _within(asyncio.to_thread(legacy_names.touch_sync, role="api"), bound)
    except TimeoutError:
        names, meta = legacy_names.unreadable(
            "TimeoutError: the old-name touch used the whole probe bound; "
            "dw_legacy_name_uses was not read"
        )
        return names, meta, []
    remaining = deadline - _monotonic()
    if remaining <= 0:
        names, meta = legacy_names.unreadable(
            "TimeoutError: the old-name touch left no time to read dw_legacy_name_uses"
        )
        return names, meta, []

    async def read() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        from ..core.database import async_session_factory

        async with async_session_factory()() as db:
            names, meta = await legacy_names.block(db)
            try:
                roles = await _roles_on_previous_address(db)
            except Exception:  # noqa: BLE001 - health never raises
                roles = []
            return names, meta, roles

    try:
        result: tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]] = await _within(
            read(), remaining
        )
        return result
    except Exception as exc:  # noqa: BLE001 - health never raises
        names, meta = legacy_names.unreadable(
            f"{type(exc).__name__}: cannot read dw_legacy_name_uses"
        )
        return names, meta, []


async def health() -> dict[str, Any]:
    settings = get_settings()
    postgres, redis_state, millm, mistudio, runner, evidence = await asyncio.gather(
        check_postgres(),
        check_redis(),
        check_http(settings.millm_base_url, "/api/health"),
        check_http(settings.mistudio_base_url, "/api/health"),
        check_runner(settings.runner_url),
        legacy_evidence(),
    )
    names, meta, roles = evidence
    facts = sibling_facts(settings, runtime_settings.overrides(), env_names.current_report(), roles)
    millm = {**millm, **facts["millm"]}
    mistudio = {**mistudio, **facts["mistudio"]}
    datajuicer = await asyncio.to_thread(check_datajuicer_worker)
    dependencies = {
        "postgres": postgres,
        "redis": redis_state,
        "data_volume": check_data_volume(),
        # The browser's address of miServer's UI, when configured: the Label step links to it.
        "millm": {**millm, "ui_url": settings.millm_ui_url} if settings.millm_ui_url else millm,
        "mistudio": mistudio,
        "datajuicer_worker": datajuicer,
        # Feature 018: reported, NOT required (FR-018.22) - the backend never depends on it.
        "runner": runner,
    }
    required = ("postgres", "redis", "data_volume")
    status = "ok" if all(dependencies[name]["ok"] for name in required) else "degraded"
    return {
        "status": status,
        "dependencies": dependencies,
        "resources": resources(),
        "operators": await asyncio.to_thread(operator_registry_summary),
        "curation_sweeper": await curation_sweeper(),
        # Feature 020 (BR-023, CF-4): exactly four keys, and everything else beside them.
        "legacy_names": names,
        "legacy_names_meta": meta,
    }
