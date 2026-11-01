"""Large result tables, paged worst first and downloadable whole (017 FR-017.58; FTID 017 §5.1).

Three tables can outgrow one answer: a version's carrier **census** (one row per genre x placement x
framing x length-band cell), a label run's **agreement** by stratum, and a version's held-out
**proof** values. Each is rebuilt from what is stored (the version's files and recorded
configuration; the run's labels; the stored proof) in the order its own answer uses (worst first),
so a page and the download are the same rows. A page carries ``next_page_token`` until the end; a
signed download (``split_download.mint_result``) streams the whole table as JSON Lines.
"""

from __future__ import annotations

from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import AppError, NotFoundError
from ...core.storage import resolve_under_data_dir
from ...models.enums import VersionState

KINDS = ("census", "agreement", "proof")
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def _next(tool: str, args: dict[str, Any], why: str) -> dict[str, Any]:
    return {"tool": tool, "args": args, "why": why}


async def _version(db: AsyncSession, version_id: str) -> Any:
    from .. import version_read_service

    version = await version_read_service.get_version_row(db, version_id)
    if version.state == VersionState.DELETED:
        raise AppError(
            f"Version {version.number} was deleted; its rows are gone.",
            code="version_deleted",
            status_code=409,
        )
    return version


def _census_rows(version: Any) -> list[dict[str, Any]]:
    from ..version_finalize import version_census

    written = [(s["name"], resolve_under_data_dir(s["path"])) for s in version.splits]
    full = version_census(written, version.behaviour_configuration, listed=1_000_000)
    if full is None:
        raise AppError(
            "This version has no carrier census: it was not built from a carrier plan, or its "
            "rows lack the census columns.",
            code="census_not_recorded",
            status_code=409,
            details={
                "version_id": version.id,
                "next": _next(
                    "dataworks_get_behaviour_status",
                    {"behaviour_key": "<the behaviour's key>"},
                    "find the behaviour's versions that carry a census",
                ),
            },
        )
    return list(full["cells"])


def _proof_rows(version: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in version.splits:
        proof = split.get("proof")
        if not proof:
            continue
        for column, values in sorted((proof.get("held_out_values") or {}).items()):
            for value in values:
                rows.append(
                    {"split": split["name"], "column": column, "value": value, "kind": "held_out"}
                )
        for item in proof.get("near_values") or []:
            rows.append({"split": split["name"], "kind": "near_value", **item})
    if not rows:
        raise AppError(
            "This version has no value-directed held-out split, so it has no proof values.",
            code="proof_not_recorded",
            status_code=409,
            details={
                "version_id": version.id,
                "next": _next(
                    "dataworks_get_version",
                    {"version_id": version.id},
                    "read the version's splits and how each was chosen",
                ),
            },
        )
    return rows


async def _agreement_rows(db: AsyncSession, run_id: str, options: dict[str, Any]) -> list[Any]:
    from ...services import label_agreement
    from ...services import label_run_service as svc

    run = await svc.get_run(db, run_id)
    cells: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        out = await label_agreement.agreement(
            db,
            run,
            options.get("column"),
            options.get("positive_value"),
            by=options.get("by"),
            limit=label_agreement.MAX_CELL_LIMIT,
            page_token=token,
        )
        strata = out.get("strata")
        if not strata:
            raise AppError(
                out.get("reason") or "This run has no agreement by stratum to list.",
                code="agreement_not_stratified",
                status_code=409,
                details={
                    "label_run_id": run_id,
                    "next": _next(
                        "dataworks_get_label_run_agreement",
                        {"run_id": run_id, "by": ["genre"]},
                        "read the run's agreement with the strata it can be split by",
                    ),
                },
            )
        cells.extend(strata["cells"])
        token = strata.get("next_page_token")
        if token is None:
            return cells


async def full_table(
    db: AsyncSession, kind: str, object_id: str, options: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every row of the table, worst first (the order of the answer that lists it)."""
    if kind == "agreement":
        return await _agreement_rows(db, object_id, options)
    if kind not in KINDS:
        raise NotFoundError(f"No result table {kind!r}; the tables are {list(KINDS)}.")
    version = await _version(db, object_id)
    if kind == "census":
        return await run_in_threadpool(_census_rows, version)
    return _proof_rows(version)


def _offset(token: str | None) -> int:
    if token is None:
        return 0
    if not token.startswith("o:") or not token[2:].isdigit():
        raise AppError(
            f"page_token {token!r} is not one this answer gave.",
            code="page_token_invalid",
            status_code=422,
            details={"page_token": token},
        )
    return int(token[2:])


async def page(
    db: AsyncSession,
    kind: str,
    object_id: str,
    options: dict[str, Any],
    *,
    limit: int,
    page_token: str | None,
) -> dict[str, Any]:
    rows = await full_table(db, kind, object_id, options)
    start = _offset(page_token)
    cut = rows[start : start + limit]
    after = start + len(cut)
    more = after < len(rows)
    return {
        "kind": kind,
        "id": object_id,
        "options": options,
        "rows_total": len(rows),
        "rows": cut,
        "order": "worst first",
        "next_page_token": f"o:{after}" if more else None,
        "next": (
            _next(
                "dataworks_get_result_page",
                {"kind": kind, "id": object_id, "page_token": f"o:{after}", **options},
                "the next page",
            )
            if more
            else _next(
                "dataworks_get_result_download_url",
                {"kind": kind, "id": object_id, **options},
                "the whole table as one file",
            )
        ),
    }
