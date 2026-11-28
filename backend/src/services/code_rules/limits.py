"""The runner limits a request sends, and the one place a runner client is made (feature 018).

``in_force()`` reads the settings (``CODE_RULE_*``; never below the contract floor, ``core.config``)
for dry runs and builds; ``floor()`` is the published floor itself, what a SAVE runs a document's
cases at (FR-018.32: a case must pass at the floor, not merely this deployment's limits). The
prelude's fuel is not a setting: every request grants the floor.

``client()`` builds the API's runner client from ``RUNNER_URL``; tests swap it with
:func:`install_client`. An unset address is :func:`errors.runner_unavailable` (the backend runs
without a runner, FR-018.22).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from ...clients.runner_client import AsyncRunnerClient, RunnerClient
from ...core.config import get_settings
from ...runner_contract.document import PACKAGED_CONTRACT_PATH
from .errors import runner_unavailable


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    """The packaged runner contract (``docs/schemas/midataworks-runner-v1.json``'s copy)."""
    data: dict[str, Any] = json.loads(PACKAGED_CONTRACT_PATH.read_text(encoding="utf-8"))
    return data


def floor() -> dict[str, int]:
    """The contract floor, as the seven request limits (wall time: the setting's backstop)."""
    published = contract()["limits_floor"]
    limits = {k: int(published[k]) for k in contract()["request_limits"] if k in published}
    limits["wall_ms"] = get_settings().code_rule_wall_ms
    return limits


def in_force() -> dict[str, int]:
    """The limits in force here (settings), as the seven request limits."""
    settings = get_settings()
    return {
        "call_fuel": settings.code_rule_call_fuel,
        "load_fuel": settings.code_rule_load_fuel,
        "prelude_fuel": int(contract()["limits_floor"]["prelude_fuel"]),
        "memory_bytes": settings.code_rule_memory_bytes,
        "input_code_points": settings.code_rule_input_code_points,
        "output_bytes": settings.code_rule_output_bytes,
        "wall_ms": settings.code_rule_wall_ms,
    }


def _default_async() -> AsyncRunnerClient:
    settings = get_settings()
    if not settings.runner_url:
        raise runner_unavailable("RUNNER_URL is not configured")
    return AsyncRunnerClient(
        settings.runner_url,
        budget_s=settings.code_rule_wall_ms / 1000,
        timeout_s=settings.code_rule_api_request_s,
    )


def _default_sync() -> RunnerClient:
    settings = get_settings()
    if not settings.runner_url:
        raise runner_unavailable("RUNNER_URL is not configured")
    return RunnerClient(
        settings.runner_url,
        budget_s=settings.code_rule_wall_ms / 1000,
        timeout_s=settings.code_rule_api_request_s,
    )


_async_factory: Callable[[], AsyncRunnerClient] = _default_async
_sync_factory: Callable[[], RunnerClient] = _default_sync


def client() -> AsyncRunnerClient:
    """The API's runner client (async), from settings unless a test installed another."""
    return _async_factory()


def sync_client() -> RunnerClient:
    """The workers' runner client (sync), from settings unless a test installed another."""
    return _sync_factory()


def install_client(
    async_factory: Callable[[], AsyncRunnerClient] | None = None,
    sync_factory: Callable[[], RunnerClient] | None = None,
) -> None:
    """Tests only: swap the factories (``None`` restores the settings-built default)."""
    global _async_factory, _sync_factory
    _async_factory = async_factory or _default_async
    _sync_factory = sync_factory or _default_sync
