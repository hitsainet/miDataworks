"""Shared list paging (FTDD 002 section 5.1): ``?page=&limit=`` with ``limit <= 200``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from fastapi import Query
from pydantic import BaseModel

from ....schemas.behaviour import BEHAVIOUR_KEY_PATTERN

MAX_LIMIT = 200
T = TypeVar("T")


@dataclass(frozen=True)
class Page:
    page: int
    limit: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


def paging(
    page: int = Query(1, ge=1, le=1_000_000),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
) -> Page:
    return Page(page=page, limit=limit)


DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class OptionalPage:
    """Paging a list route that answered everything before 017 (FR-017.53, FTID 017 §5.3).

    Asked for (``page`` or ``limit`` given; every MCP list tool sends both), the route answers one
    page and says how many there are; not asked for, it answers everything as before, so the
    screens that read these lists whole are unchanged.
    """

    page: int | None
    limit: int | None

    @property
    def asked(self) -> bool:
        return self.page is not None or self.limit is not None

    def resolved(self) -> Page:
        return Page(page=self.page or 1, limit=self.limit or DEFAULT_LIMIT)

    def cut(self, items: list[T]) -> tuple[list[T], dict[str, int]]:
        """``(the page's items, {"total", "page", "limit"})``; everything and ``{}`` unasked."""
        if not self.asked:
            return items, {}
        p = self.resolved()
        return items[p.offset : p.offset + p.limit], {
            "total": len(items),
            "page": p.page,
            "limit": p.limit,
        }


def optional_paging(
    page: int | None = Query(None, ge=1, le=1_000_000, description="Page number (from 1)."),
    limit: int | None = Query(
        None, ge=1, le=MAX_LIMIT, description=f"Page size (default {DEFAULT_LIMIT} when paging)."
    ),
) -> OptionalPage:
    return OptionalPage(page=page, limit=limit)


class Paged[T](BaseModel):
    """One page of a list route that answers a bare list when not asked to page."""

    items: list[T]
    total: int
    page: int
    limit: int


def paged_or_all[T](items: list[T], paging: OptionalPage) -> list[T] | Paged[T]:
    if not paging.asked:
        return items
    cut, meta = paging.cut(items)
    return Paged[T](items=cut, **meta)


def behaviour_filter(
    behaviour_key: str | None = Query(
        None,
        pattern=BEHAVIOUR_KEY_PATTERN,
        description="Only objects for this behaviour (017 FR-017.53).",
    ),
) -> str | None:
    """The behaviour filter every keyed list takes (017 FR-017.53)."""
    return behaviour_key
