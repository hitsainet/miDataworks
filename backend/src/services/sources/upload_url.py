"""Signed, single-use upload URLs (G-3, FU-7a; operator decision 2026-11-25).

``dataworks_upload_file`` carries the file as base64 inside the MCP call, so a source of several
MB cannot pass through an agent (every byte is in its call, and the MCP request caps at 10 MiB).
``POST /api/v1/sources/upload-url`` instead records an upload (``dw_upload_urls``) and signs a URL;
the agent sends the file with ONE plain ``curl -T <file> <url>`` to the miDataworks LAN host, and
``PUT /api/v1/uploads/{token}`` streams it into the same import path as a multipart upload.

The token is ``split_download``'s design: ``<payload>.<HMAC-SHA256>``, the key derived from
``SETTINGS_ENCRYPTION_KEY`` under its OWN label (a download token never verifies as an upload
token, nor the reverse). The payload binds the row id, the byte cap and the expiry. The row makes
it single use: the PUT claims it with a conditional UPDATE before reading a byte, so a second PUT
(or a retry after a failure) is refused ``upload_url_used`` and asks for a new URL. No approval
action (decision 2026-11-25): issuing is logged (the row and a log line), and builds, publishes and
GPU work stay gated as they are.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.config import env_settings, get_settings
from ...core.errors import AppError
from ...models.upload_url import UploadUrl
from . import upload_service

logger = logging.getLogger(__name__)

UPLOAD_PATH = "/api/v1/uploads/"
ALL_EXTENSIONS = sorted({e for exts in upload_service.EXTENSIONS.values() for e in exts})


def _now() -> float:
    """The clock tokens are checked against (one seam, so a test can move it)."""
    return time.time()


def _key() -> bytes:
    material = env_settings().settings_encryption_key.get_secret_value().encode("utf-8")
    return hmac.new(material, b"midataworks-source-upload-v1", "sha256").digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_key(), body.encode(), 'sha256').digest())}"


def _check_name(filename: str) -> None:
    import os

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALL_EXTENSIONS:
        raise AppError(
            f"{filename} does not end in one of {', '.join(ALL_EXTENSIONS)}; name the file by "
            "its format (Parquet, JSON Lines or CSV).",
            code="upload_format_mismatch",
            status_code=422,
            details={"file": filename, "extensions": ALL_EXTENSIONS},
        )


async def mint(
    db: AsyncSession,
    *,
    filename: str,
    split: str,
    display_name: str | None,
    csv: dict[str, Any] | None,
    expected_bytes: int,
    expected_sha256: str | None,
    expires_in_s: int,
    cap: int,
    who: Who,
    now: float | None = None,
) -> dict[str, Any]:
    """Record the upload and sign its URL. Refuses a file the upload limit would refuse anyway
    (``upload_too_large``), before anything is sent."""
    _check_name(filename)
    if expected_bytes > cap:
        raise AppError(
            f"{filename} is {expected_bytes:,} bytes, over the {cap:,}-byte upload limit. Split "
            "it, or raise the limit in Settings → Environment.",
            code="upload_too_large",
            status_code=413,
            details={"file": filename, "limit_bytes": cap, "bytes": expected_bytes},
        )
    longest = get_settings().upload_url_max_expiry_s
    if expires_in_s > longest:
        raise AppError(
            f"A signed upload URL lives at most {longest} s (UPLOAD_URL_MAX_EXPIRY_S).",
            code="upload_url_expiry_invalid",
            status_code=422,
            details={"setting": "UPLOAD_URL_MAX_EXPIRY_S", "limit": longest},
        )
    expires = int((now if now is not None else _now()) + expires_in_s)
    row = UploadUrl(
        id=f"uu_{uuid.uuid4().hex}",
        filename=filename,
        split=split,
        display_name=display_name,
        csv=csv,
        max_bytes=expected_bytes,
        expected_sha256=expected_sha256,
        expires_at=datetime.fromtimestamp(expires, tz=UTC),
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    await db.flush()
    token = sign({"u": row.id, "b": expected_bytes, "e": expires})
    path = UPLOAD_PATH + token
    base = (get_settings().lan_api_url or "").rstrip("/")
    url = f"{base}{path}" if base else path
    logger.info(
        "upload URL %s issued to %s (%s): %s as split %s, at most %d bytes, expires %d",
        row.id,
        who.who,
        who.origin,
        filename,
        split,
        expected_bytes,
        expires,
    )
    return {
        "upload_url_id": row.id,
        "url": url,
        "path": path,
        "absolute": bool(base),
        "method": "PUT",
        "expires_at": expires,
        "max_bytes": expected_bytes,
        "filename": filename,
        "split": split,
        "curl": f"curl -T <path to {filename}> '{url}'",
        "note": (
            "One PUT, once, from the LAN: the URL works one time, and a request through the "
            "public proxy is refused (upload_url_lan_only)."
            if base
            else "LAN_API_URL is not set: prefix the path with the miDataworks LAN address. One "
            "PUT, once, from the LAN."
        ),
    }


def verify(token: str, now: float | None = None) -> dict[str, Any]:
    """The signed payload; ``403 upload_token_invalid`` or ``410 upload_url_expired``."""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_key(), body.encode(), "sha256").digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("signature")
        payload: dict[str, Any] = json.loads(_unb64(body))
        str(payload["u"]), int(payload["b"]), int(payload["e"])  # noqa: B018 - shape check
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        raise AppError(
            "This upload link is not valid. Ask for a new one with dataworks_get_upload_url.",
            code="upload_token_invalid",
            status_code=403,
        ) from None
    if int(payload["e"]) < (now if now is not None else _now()):
        raise AppError(
            "This upload link has expired. Ask for a new one with dataworks_get_upload_url.",
            code="upload_url_expired",
            status_code=410,
        )
    return payload


def refuse_public(request: Any) -> None:
    """``403 upload_url_lan_only`` for a request the public proxy forwarded (decision 2026-11-25:
    an agent uploads to the miDataworks LAN host only). Cloudflare adds ``cf-ray`` and
    ``cf-connecting-ip`` to every request it forwards; a LAN request carries neither."""
    # literal names, so test_request_headers_read.py sees each read
    if request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"):
        raise AppError(
            "Signed uploads are taken on the LAN only. Send the file to the miDataworks LAN "
            "address (the url dataworks_get_upload_url answered), not through the public host.",
            code="upload_url_lan_only",
            status_code=403,
        )


async def claim(db: AsyncSession, payload: dict[str, Any]) -> UploadUrl:
    """Spend the URL before a byte is read: one conditional UPDATE, committed at once."""
    upload_id = str(payload["u"])
    result = await db.execute(
        update(UploadUrl)
        .where(UploadUrl.id == upload_id, UploadUrl.used_at.is_(None))
        .values(used_at=datetime.now(tz=UTC))
        .returning(UploadUrl.id)
    )
    claimed = result.scalar_one_or_none()
    await db.commit()
    row = await db.get(UploadUrl, upload_id)
    if row is None:
        raise AppError(
            "This upload link is not valid. Ask for a new one with dataworks_get_upload_url.",
            code="upload_token_invalid",
            status_code=403,
        )
    if claimed is None:
        raise AppError(
            f"This upload link was used at {row.used_at:%Y-%m-%d %H:%M:%S} UTC; a link works "
            "once. Ask for a new one with dataworks_get_upload_url.",
            code="upload_url_used",
            status_code=409,
            details={"upload_url_id": row.id},
        )
    if int(payload["b"]) != int(row.max_bytes):
        raise AppError(
            "This upload link does not match its record.",
            code="upload_token_invalid",
            status_code=403,
        )
    return row


class RequestBody:
    """``read(n)`` over a Starlette request's body stream, for ``upload_service.receive_stream``."""

    def __init__(self, request: Any) -> None:
        self._chunks = request.stream().__aiter__()
        self._buffer = b""
        self._done = False

    async def read(self, size: int) -> bytes:
        while not self._done and len(self._buffer) < size:
            try:
                self._buffer += await self._chunks.__anext__()
            except StopAsyncIteration:
                self._done = True
        out, self._buffer = self._buffer[:size], self._buffer[size:]
        return out
