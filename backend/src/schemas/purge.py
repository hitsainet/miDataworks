"""Purge requests and results (operator decision 2026-10-20)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PurgeKind = Literal["dataset", "label_run", "generation_run", "approval", "rule_set", "code_rule"]


class PurgeItem(BaseModel):
    kind: PurgeKind
    id: str = Field(min_length=1, max_length=64)


class PurgeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class PurgeBatchRequest(BaseModel):
    items: list[PurgeItem] = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


class PurgePlanRequest(BaseModel):
    items: list[PurgeItem] = Field(min_length=1, max_length=200)


class BlockerOut(BaseModel):
    code: str
    message: str
    table: str | None
    ids: list[str]


class RemovedWith(BaseModel):
    """The item of the same batch whose purge already removed this one."""

    kind: str
    id: str
    name: str | None


class PurgeOutcome(BaseModel):
    """One item. ``status``: ``purgeable`` (a plan), ``purged``, or ``blocked`` (``code`` says
    why: ``not_archived``, ``purge_blocked`` with ``blockers``, ``not_found``).

    A batch item that an earlier item of the same batch removed with its cascade is ``purged``
    with ``code`` ``removed_with`` and ``removed_with`` naming that item; its ``tombstone_id`` is
    that item's tombstone, which counts it."""

    kind: str
    id: str
    name: str | None
    status: Literal["purgeable", "purged", "blocked"]
    code: str | None
    message: str | None
    blockers: list[BlockerOut]
    counts: dict[str, int]
    bytes: int
    paths: int
    paths_left: list[str]
    tombstone_id: str | None
    removed_with: RemovedWith | None = None


class PurgeBatchOut(BaseModel):
    items: list[PurgeOutcome]
    purged: int
    blocked: int
    bytes: int
