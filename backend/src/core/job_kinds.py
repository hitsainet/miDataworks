"""The job-kind registry (ADR-007; Foundation tasks 5.2, 5.2a).

Every job kind declares the three things its lifecycle needs: a cancel scope, a janitor heartbeat
limit and a Socket.IO room. ``tests/unit/test_job_kind_registry.py`` reads this live registry and
fails if a kind lacks any of them, and :func:`register_job_kind` refuses a declaration that is
incomplete, so a kind cannot be half-registered.

Feature hand-off: a feature adds its kind here (or calls :func:`register_job_kind` from a module
the app imports at start) — never a private table of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from .cancellation import SCOPES

_ROOM = re.compile(r"^dataworks/[a-z0-9][a-z0-9-]*/\{id\}$")


@dataclass(frozen=True)
class JobKind:
    name: str
    cancel_scope: str
    janitor_heartbeat_limit: timedelta
    room_template: str
    #: The Celery task that runs it, or None when a feature has not built it yet.
    task_name: str | None = None
    description: str = ""

    def room(self, job_id: str) -> str:
        return self.room_template.replace("{id}", job_id)


JOB_KINDS: dict[str, JobKind] = {}


class JobKindError(ValueError):
    """A job kind declaration is incomplete or conflicts with another."""


def register_job_kind(kind: JobKind) -> JobKind:
    """Add a kind to the live registry, refusing an incomplete or duplicate declaration."""
    if not kind.name or not re.fullmatch(r"[a-z][a-z0-9_]*", kind.name):
        raise JobKindError(f"job kind name {kind.name!r} must be snake_case")
    if kind.cancel_scope not in SCOPES:
        raise JobKindError(f"{kind.name}: cancel scope {kind.cancel_scope!r} is not registered")
    if kind.janitor_heartbeat_limit <= timedelta(0):
        raise JobKindError(f"{kind.name}: janitor heartbeat limit must be positive")
    if not _ROOM.fullmatch(kind.room_template):
        raise JobKindError(
            f"{kind.name}: room {kind.room_template!r} must look like dataworks/<kind>/{{id}}"
        )
    if kind.task_name is not None and not kind.task_name.startswith("midataworks."):
        raise JobKindError(f"{kind.name}: task {kind.task_name!r} must start with midataworks.")
    existing = JOB_KINDS.get(kind.name)
    if existing is not None and existing != kind:
        raise JobKindError(f"job kind {kind.name!r} is already registered differently")
    JOB_KINDS[kind.name] = kind
    return kind


def get_job_kind(name: str) -> JobKind:
    try:
        return JOB_KINDS[name]
    except KeyError:
        raise JobKindError(f"unknown job kind {name!r}") from None


# --- Foundation's kinds ---------------------------------------------------------------------

SELFTEST = register_job_kind(
    JobKind(
        name="selftest",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=5),
        room_template="dataworks/selftest/{id}",
        task_name="midataworks.selftest.run",
        description="Walking skeleton: writes Parquet, reads it with DuckDB, reports, cancels.",
    )
)

#: Feature 005's label runs (Stage 3, requested by 005).
LABEL_RUN = register_job_kind(
    JobKind(
        name="label_run",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/label-runs/{id}",
        description="Classifier or judge labeling run (feature 005).",
    )
)

#: The shared miLLM model-lease holder: renewal and crash cleanup (Stage 3, requested by 005).
LEASE = register_job_kind(
    JobKind(
        name="lease",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=10),
        room_template="dataworks/leases/{id}",
        description="Holds a miLLM model lease for the jobs that need that model (ADR-012).",
    )
)
