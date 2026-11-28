"""Signed uploads (G-3, operator decision 2026-11-25): the token IS the authorisation.

``PUT /api/v1/uploads/{token}`` takes the one file ``POST /api/v1/sources/upload-url`` signed
(``services/sources/upload_url.py``), streams it with the URL's byte cap and starts the same
``source_import`` job a multipart upload starts (``sources._start_upload_job``), recorded as the
actor who asked for the URL. No MCP tool calls it: the tool returns the URL and the agent sends the
file with one ``curl -T``, so the bytes never pass through the MCP transport (the ledger exempts it
with that reason). A request through the public proxy is refused: uploads are taken on the LAN.
"""

from __future__ import annotations

import json
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Who
from ....core.database import get_db
from ....core.errors import AppError
from ....core.storage import staging_dir
from ....services.sources import upload_service, upload_url

router = APIRouter(prefix="/api/v1/uploads", tags=["sources"])


@router.put("/{token}", status_code=202, response_model=None)
async def put_upload(token: str, request: Request, db: AsyncSession = Depends(get_db)) -> Any:
    upload_url.refuse_public(request)
    payload = upload_url.verify(token)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > int(payload["b"]):
        raise AppError(
            f"The request is {int(declared):,} bytes; this link takes at most {int(payload['b']):,}. "
            "Ask for a new link with the file's real size.",
            code="upload_too_large",
            status_code=413,
            details={"limit_bytes": int(payload["b"]), "bytes": int(declared)},
        )
    row = await upload_url.claim(db, payload)  # spent before a byte is read
    upload_dir = f"upload_{uuid.uuid4().hex}"
    directory = staging_dir() / upload_dir
    staged = "000.bin"
    try:
        received = await upload_service.receive_stream(
            upload_url.RequestBody(request), directory / staged, int(row.max_bytes), row.filename
        )
        if row.expected_sha256 and received.sha256 != row.expected_sha256:
            raise AppError(
                f"{row.filename} has SHA-256 {received.sha256}, not the {row.expected_sha256} "
                "the link was asked for. Check the file and ask for a new link.",
                code="upload_hash_mismatch",
                status_code=422,
                details={"expected": row.expected_sha256, "actual": received.sha256},
            )
        fmt = upload_service.sniff_format(received.path)
        upload_service.check_extension(row.filename, fmt)
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    from .sources import _start_upload_job

    entries = [
        {
            "name": row.filename,
            "split": row.split,
            "staged": staged,
            "sha256": received.sha256,
            "bytes": received.bytes,
            "parse_options": upload_service.parse_options(fmt, row.csv),
        }
    ]
    who = Who(row.created_by, row.created_by_origin)  # type: ignore[arg-type]
    response = await _start_upload_job(db, who, upload_dir, entries, row.display_name, row.csv)
    answer = json.loads(bytes(response.body))
    row.job_id = answer.get("job_id")
    row.source_id = answer.get("source_id") or answer.get("id")
    await db.commit()
    return response
