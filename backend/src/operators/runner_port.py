"""This feature's view of the isolated runner (feature 018; FTID §3.4, decision I2).

``RunContext.runner`` reads :func:`resolver`. With nothing installed it answers the DEFAULT client
built from settings (``services.code_rules.limits.sync_client``: ``RUNNER_URL``, the retry budget
and the request timeout) - unlike ``endpoint_port``, whose default refuses until each process
installs a resolver, so no curation or preview worker can be left without a runner by a missing
install line ("declaring a mechanism is not wiring it"). An unset ``RUNNER_URL`` raises there, and
``rule_set@1`` reads that as the runner being unavailable (every code-rule row
``rule_worker_unavailable``, FR-018.22), never as a failed build.

Tests swap the client with :func:`install_resolver` and restore it with ``install_resolver(None)``.
No manifest field changes for this, so no operator's ``manifest_hash`` moves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..clients.runner_client import RunnerClient

_installed: RunnerClient | None = None


def default() -> RunnerClient:
    """The settings-built client (imported at call time: the operators package loads first)."""
    from ..services.code_rules import limits

    return limits.sync_client()


def resolver() -> RunnerClient:
    """The installed client, else the default from settings."""
    return _installed if _installed is not None else default()


def install_resolver(client: RunnerClient | None) -> None:
    """Tests only: install a client (``None`` restores the default)."""
    global _installed
    _installed = client
