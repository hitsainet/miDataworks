"""Large result tables (017 FR-017.58; FTID 017 §5.1): a page, worst first, and a signed download.

``GET /api/v1/results/{kind}/{id}`` answers ``limit`` rows of the table from ``page_token`` on with
``next_page_token``; ``POST /api/v1/results/{kind}/{id}/download-url`` signs a link that streams the
whole table (``GET /api/v1/downloads/{token}``). ``kind`` is ``census`` or ``proof`` (``id`` a
version) or ``agreement`` (``id`` a label run, shaped by ``column``, ``positive_value`` and ``by``).
Reads only; nothing is stored.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....services import split_download
from ....services.behaviour import results

router = APIRouter(prefix="/api/v1/results", tags=["behaviours"])
#: A version id (UUID hex) or a label run id (lr_...).
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"

Kind = Literal["census", "agreement", "proof"]


class ResultDownloadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str | None = Field(None, max_length=256)
    positive_value: str | None = Field(None, max_length=256)
    by: list[str] | None = Field(None, max_length=8)
    expires_in_s: int = Field(3600, ge=60, le=86_400)


def _options(
    column: str | None, positive_value: str | None, by: list[str] | None
) -> dict[str, Any]:
    """Only the agreement table is shaped by options; the others take none."""
    out: dict[str, Any] = {}
    if column is not None:
        out["column"] = column
    if positive_value is not None:
        out["positive_value"] = positive_value
    if by:
        out["by"] = list(by)
    return out


@router.get("/{kind}/{object_id}")
async def result_page(
    kind: Kind,
    object_id: str = Path(..., pattern=ID_PATTERN),
    column: str | None = Query(None, max_length=256),
    positive_value: str | None = Query(None, max_length=256),
    by: list[str] | None = Query(None, max_length=8),
    limit: int = Query(results.DEFAULT_LIMIT, ge=1, le=results.MAX_LIMIT),
    page_token: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One page of the table, worst first, with ``rows_total`` and ``next_page_token``."""
    options = _options(column, positive_value, by) if kind == "agreement" else {}
    return await results.page(db, kind, object_id, options, limit=limit, page_token=page_token)


@router.post("/{kind}/{object_id}/download-url")
async def result_download_url(
    kind: Kind,
    body: ResultDownloadIn,
    object_id: str = Path(..., pattern=ID_PATTERN),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """A signed, expiring link that streams the whole table as JSON Lines (the rows a full
    paging would give, in the same order)."""
    options = _options(body.column, body.positive_value, body.by) if kind == "agreement" else {}
    rows = await results.full_table(db, kind, object_id, options)
    return await run_in_threadpool(
        split_download.mint_result, kind, object_id, options, len(rows), body.expires_in_s
    )
