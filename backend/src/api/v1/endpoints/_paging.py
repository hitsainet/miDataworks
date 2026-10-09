"""Shared list paging (FTDD 002 section 5.1): ``?page=&limit=`` with ``limit <= 200``."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query

MAX_LIMIT = 200


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
