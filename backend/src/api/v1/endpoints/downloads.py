"""Signed split downloads (PII run G8, 2026-10-20): the token IS the authorisation.

``GET /api/v1/downloads/{token}`` streams the split ``POST /versions/{id}/splits/{split}/
download-url`` signed (``services/split_download.py``). No MCP tool calls it: the tool returns the
URL and the client fetches it directly, so the bytes never pass through the MCP transport (the
ledger exempts it with that reason).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ....core.database import get_db
from ....core.storage import tmp_dir
from ....services import split_download, version_read_service

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])


@router.get("/{token}", response_model=None)
async def download_split(token: str, db: AsyncSession = Depends(get_db)) -> Any:
    payload = split_download.verify(token)
    version = await version_read_service.get_version_row(db, str(payload["v"]))
    path = split_download.split_file(version, payload)
    name = f"{version.id}-{payload['s']}"
    if payload["f"] == "jsonl":
        return StreamingResponse(
            split_download.jsonl(path, payload["c"]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{name}.jsonl"'},
        )
    out_dir = tmp_dir() / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{uuid.uuid4().hex}.parquet"
    served = await run_in_threadpool(split_download.parquet, path, payload["c"], out)
    cleanup = BackgroundTask(out.unlink, missing_ok=True) if served == out else None
    return FileResponse(
        served,
        media_type="application/vnd.apache.parquet",
        filename=f"{name}.parquet",
        background=cleanup,
    )
