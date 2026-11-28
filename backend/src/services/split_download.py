"""Signed, expiring downloads of one version split (PII run G8, 2026-10-20).

An MCP client paged ``dataworks_get_version_rows`` 41 times, every page carrying the full source
documents, to read two columns. ``POST /versions/{id}/splits/{split}/download-url`` mints a token;
``GET /api/v1/downloads/{token}`` streams the split (Parquet or JSON Lines, optionally projected)
to whoever holds it until it expires. Nothing is written to a row, and no credential travels: the
token is ``<payload>.<HMAC-SHA256>`` with a key derived from ``SETTINGS_ENCRYPTION_KEY`` under its
own label, and the payload binds the version, split, columns, format and expiry, so a token cannot
be widened to another split or column set. A deleted version refuses even an unexpired token.

FU-7a gap (af), 2026-11-27: the default link serves the VERSION's split file as built (every
``_dw_*`` column), which is not the file a publish pushed (the build's projection). The answer now
says what it serves (``served``, and for a whole file its ``sha256``, ``bytes`` and
``file_columns``), and ``publish_id`` signs a link to that publish's own file of the split, kept
under ``publish/<build id>/``, byte for byte what the Hub holds.
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


def _refuse_not_practice(version: Version, chosen: dict[str, Any]) -> None:
    """021 FR-021.14: a download FOR PRACTICE of a split that is not a WSP practice partition is
    refused, naming the practice splits. The signed payload is unchanged: the purpose is a check,
    not a capability (R1A-D20)."""
    practice = [
        s["name"] for s in version.splits if (s.get("wsp") or {}).get("role") == "wsp_practice"
    ]
    role = (chosen.get("wsp") or {}).get("role")
    if role == "wsp_practice":
        return
    if role == "wsp_heldout":
        code = "wsp_heldout_as_practice"
        message = (
            f"Split {chosen['name']!r} is a WSP held-out partition: it may be evaluated on, never "
            f"fitted on. Practice partitions of this version: {practice or 'none'}."
        )
    else:
        code = "not_a_wsp_partition"
        message = (
            f"Split {chosen['name']!r} is not a WSP partition. Practice partitions of this "
            f"version: {practice or 'none'}."
        )
    raise AppError(
        message,
        code=code,
        status_code=409,
        details={"split": chosen["name"], "practice_splits": practice},
    )


#: Publish states whose split files are on the Hub (``no_change``: identical files already were).
SERVED_PUBLISH_STATES = ("published", "no_change")


def published_file(session: Any, version: Version, split: str, publish_id: str) -> dict[str, Any]:
    """The local copy of the file ``publish_id`` pushed for ``split``: ``{path, sha256, bytes,
    publish_id, build_id}``, or the refusal that says why there is none."""
    from ..models.publish import Publish, PublishBuild, PublishKind
    from .publishing.build_service import build_file_path

    publish = session.get(Publish, publish_id)
    if publish is None:
        raise AppError(
            f"No publish {publish_id}. dataworks_list_publishes(version_id=...) lists them.",
            code="publish_not_found",
            status_code=404,
        )
    if str(publish.version_id) != str(version.id):
        raise AppError(
            f"Publish {publish_id} pushed version {publish.version_id}, not {version.id}.",
            code="publish_other_version",
            status_code=409,
        )
    if publish.kind == PublishKind.CARD_ONLY or publish.status not in SERVED_PUBLISH_STATES:
        raise AppError(
            f"Publish {publish_id} is {publish.status} ({publish.kind}): it put no split file on "
            "the Hub to serve.",
            code="publish_not_published",
            status_code=409,
            details={"status": publish.status, "kind": publish.kind},
        )
    build = session.get(PublishBuild, publish.build_id)
    entry = next((f for f in (build.files or []) if f["name"] == split), None) if build else None
    if build is None or entry is None:
        raise AppError(
            f"Publish {publish_id} carried no split {split!r}.",
            code="split_not_found",
            status_code=404,
        )
    path = build_file_path(build, str(entry["path"]))
    if not path.is_file():
        raise AppError(
            f"The local copy of {entry['path']} for publish {publish_id} is gone.",
            code="publish_file_missing",
            status_code=410,
        )
    return {
        "path": path,
        "sha256": str(entry["sha256"]),
        "bytes": int(entry["bytes"]),
        "rows": int(entry["rows"]),
        "publish_id": publish.id,
        "build_id": build.id,
        "repo_path": str(entry["path"]),
    }


def mint(
    version: Version,
    split: str,
    columns: list[str] | None,
    fmt: str,
    expires_in_s: int,
    now: float | None = None,
    purpose: str | None = None,
    published: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check the split and columns exist, then sign; returns the URL facts. ``published`` (from
    :func:`published_file`) signs that publish's file instead of the version's."""
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
    if purpose == "wsp_practice":
        _refuse_not_practice(version, chosen)
    if published is not None and (columns or fmt != "parquet"):
        raise AppError(
            "A published file is served whole, as Parquet: leave out columns and format, or "
            "leave out publish_id for the version's own file.",
            code="publish_file_whole",
            status_code=422,
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
    payload: dict[str, Any] = {"v": version.id, "s": split, "c": columns, "f": fmt, "e": expires}
    if published is not None:  # only then: every default token keeps its exact payload
        payload["p"] = published["publish_id"]
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
        "rows": int(chosen["rows"]) if published is None else published["rows"],
        "columns": columns,
        "format": fmt,
        **_served(chosen, columns, fmt, published),
        "note": (
            None
            if base
            else "PUBLIC_API_URL is not set: prefix the path with the miDataworks address you reach."
        ),
    }


def _served(
    chosen: dict[str, Any],
    columns: list[str] | None,
    fmt: str,
    published: dict[str, Any] | None,
) -> dict[str, Any]:
    """What the link's bytes are. A whole Parquet file has a known ``sha256`` (the version
    split's recorded ``file_sha256``, or the publish's recorded file hash, which the publish
    compared with the Hub); a projection or JSON Lines is written at fetch time, so it has none."""
    if published is not None:
        return {
            "served": "published_file",
            "publish_id": published["publish_id"],
            "build_id": published["build_id"],
            "repo_path": published["repo_path"],
            "sha256": published["sha256"],
            "bytes": published["bytes"],
            "file_columns": _names(published["path"]),
        }
    if columns or fmt != "parquet":
        return {"served": "projection", "sha256": None, "bytes": None, "file_columns": None}
    path = resolve_under_data_dir(chosen["path"])
    return {
        "served": "version_file",
        "sha256": chosen.get("file_sha256"),
        "bytes": chosen.get("bytes"),
        "file_columns": _names(path),
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
    """Sign a download of one result table (a census, an agreement by stratum, proof values, a
    label run's scores).

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
