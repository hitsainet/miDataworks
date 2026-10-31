"""Signed, expiring downloads of one version split (PII run G8, 2026-10-20).

An MCP client paged ``dataworks_get_version_rows`` 41 times, every page carrying the full source
documents, to read two columns. ``POST /versions/{id}/splits/{split}/download-url`` mints a token;
``GET /api/v1/downloads/{token}`` streams the split (Parquet or JSON Lines, optionally projected)
to whoever holds it until it expires. Nothing is written to a row, and no credential travels: the
token is ``<payload>.<HMAC-SHA256>`` with a key derived from ``SETTINGS_ENCRYPTION_KEY`` under its
own label, and the payload binds the version, split, columns, format and expiry, so a token cannot
be widened to another split or column set. A deleted version refuses even an unexpired token.
"""

from __future__ import annotations

import base64
import hmac
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..core.config import env_settings, get_settings
from ..core.errors import AppError
from ..core.storage import resolve_under_data_dir
from ..models.enums import VersionState
from ..models.version import Version
from .duck import arrow_reader, connect, files_param, quote_ident

DOWNLOAD_PATH = "/api/v1/downloads/"
#: JSON Lines rows streamed per batch.
BATCH_ROWS = 10_000


def _key() -> bytes:
    material = env_settings().settings_encryption_key.get_secret_value().encode("utf-8")
    # HMAC as the key derivation (one hashing module rule: no hashlib.sha256 call here)
    return hmac.new(material, b"midataworks-split-download-v1", "sha256").digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint(
    version: Version,
    split: str,
    columns: list[str] | None,
    fmt: str,
    expires_in_s: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Check the split and columns exist, then sign; returns the URL facts."""
    if version.state == VersionState.DELETED:
        raise AppError(
            f"Version {version.number} was deleted; its rows are gone.",
            code="version_deleted",
            status_code=409,
        )
    chosen = next((s for s in version.splits if s["name"] == split), None)
    if chosen is None:
        raise AppError(
            f"Version {version.number} has no split {split!r}; it has "
            f"{[s['name'] for s in version.splits]}.",
            code="split_not_found",
            status_code=404,
        )
    if columns:
        names = _names(resolve_under_data_dir(chosen["path"]))
        unknown = [c for c in columns if c not in names]
        if unknown:
            raise AppError(
                f"The split has no column(s) {unknown}; it has {names}.",
                code="column_not_found",
                status_code=404,
                details={"columns": unknown},
            )
        columns = list(dict.fromkeys(["_dw_row_key", *columns]))
    expires = int((now if now is not None else time.time()) + expires_in_s)
    payload = {"v": version.id, "s": split, "c": columns, "f": fmt, "e": expires}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(_key(), body.encode(), "sha256").digest()
    token = f"{body}.{_b64(mac)}"
    path = DOWNLOAD_PATH + token
    base = (get_settings().public_api_url or "").rstrip("/")
    return {
        "url": f"{base}{path}" if base else path,
        "path": path,
        "absolute": bool(base),
        "expires_at": expires,
        "version_id": version.id,
        "split": split,
        "rows": int(chosen["rows"]),
        "columns": columns,
        "format": fmt,
        "note": (
            None
            if base
            else "PUBLIC_API_URL is not set: prefix the path with the miDataworks address you reach."
        ),
    }


def verify(token: str, now: float | None = None) -> dict[str, Any]:
    """The signed payload; ``403 download_token_invalid`` or ``410 download_expired``."""
    try:
        body, signature = token.split(".", 1)
        expected = hmac.new(_key(), body.encode(), "sha256").digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            raise ValueError("signature")
        payload: dict[str, Any] = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        raise AppError(
            "This download link is not valid. Ask for a new one with "
            "dataworks_get_split_download_url.",
            code="download_token_invalid",
            status_code=403,
        ) from None
    if int(payload["e"]) < (now if now is not None else time.time()):
        raise AppError(
            "This download link has expired. Ask for a new one.",
            code="download_expired",
            status_code=410,
        )
    return payload


def _names(path: Path) -> list[str]:
    con = connect()
    try:
        return [
            d[0]
            for d in con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [str(path)]).description
            or []
        ]
    finally:
        con.close()


def split_file(version: Version, payload: dict[str, Any]) -> Path:
    if version.state == VersionState.DELETED:
        raise AppError("The version was deleted.", code="version_deleted", status_code=409)
    chosen = next((s for s in version.splits if s["name"] == payload["s"]), None)
    if chosen is None:
        raise AppError("The split is gone.", code="split_not_found", status_code=404)
    return resolve_under_data_dir(chosen["path"])


def _select(path: Path, columns: list[str] | None) -> str:
    if not columns:
        return "*"
    names = _names(path)
    return ", ".join(quote_ident(c, names) for c in columns)


def jsonl(path: Path, columns: list[str] | None) -> Iterator[bytes]:
    """The split as JSON Lines, streamed in batches."""
    from .lineage_service import _json_safe

    con = connect()
    try:
        reader = arrow_reader(
            con,
            f"SELECT {_select(path, columns)} FROM read_parquet(?)",  # noqa: S608
            [files_param([path])],
            BATCH_ROWS,
        )
        for batch in reader:
            for row in batch.to_pylist():
                yield (json.dumps(_json_safe(row), ensure_ascii=False, default=str) + "\n").encode()
    finally:
        con.close()


def parquet(path: Path, columns: list[str] | None, out: Path) -> Path:
    """The split itself, or its projection written to ``out``."""
    if not columns:
        return path
    import pyarrow.parquet as pq

    con = connect()
    writer = None
    try:
        reader = arrow_reader(
            con,
            f"SELECT {_select(path, columns)} FROM read_parquet(?)",  # noqa: S608
            [files_param([path])],
            BATCH_ROWS,
        )
        for batch in reader:
            if writer is None:
                writer = pq.ParquetWriter(out, batch.schema)
            writer.write_batch(batch)
        if writer is None:  # an empty split: an empty file with the projected schema
            pq.write_table(reader.schema.empty_table(), out)
    finally:
        if writer is not None:
            writer.close()
        con.close()
    return out


# --- result tables (017 FR-017.58) ------------------------------------------------------------


def mint_result(
    kind: str,
    object_id: str,
    options: dict[str, Any],
    rows: int,
    expires_in_s: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Sign a download of one result table (a census, an agreement by stratum, proof values).

    The payload binds the table's kind, the object, the options that shape it and the expiry, so
    a token cannot be widened to another table; the table is rebuilt from what is stored when the
    link is fetched, in the order its paged answer uses, and streamed as JSON Lines."""
    expires = int((now if now is not None else time.time()) + expires_in_s)
    payload = {"k": kind, "i": object_id, "o": options, "f": "jsonl", "e": expires}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = hmac.new(_key(), body.encode(), "sha256").digest()
    path = DOWNLOAD_PATH + f"{body}.{_b64(mac)}"
    base = (get_settings().public_api_url or "").rstrip("/")
    return {
        "url": f"{base}{path}" if base else path,
        "path": path,
        "absolute": bool(base),
        "expires_at": expires,
        "kind": kind,
        "id": object_id,
        "options": options,
        "rows": rows,
        "format": "jsonl",
        "note": (
            None
            if base
            else "PUBLIC_API_URL is not set: prefix the path with the miDataworks address you reach."
        ),
    }


def result_jsonl(rows: list[dict[str, Any]]) -> Iterator[bytes]:
    from .lineage_service import _json_safe

    for row in rows:
        yield (json.dumps(_json_safe(row), ensure_ascii=False, default=str) + "\n").encode()
