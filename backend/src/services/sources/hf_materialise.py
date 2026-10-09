"""Hugging Face materialisation (FR-001.3, 001.7, 001.9, 001.10; 001 FTID section 3.7).

Pin, then read: ``load_dataset`` is always called with the resolved 40-character commit,
``trust_remote_code=False`` and a per-job ``cache_dir`` under ``runs/<job_id>/hf_cache/`` — so a cancel
cleans up only this job's cache, and nothing reads a neighbour's stale copy. Splits are written in
record batches to ``staging/<job_id>/``, fsynced and hashed from disk; row counts come from Parquet
metadata, never from ``len()`` of a dict of splits.

Refusals before any download: a loading script at the repository root (``remote_code_refused``); free
space below 2.2x the expected bytes (``insufficient_space``); more than ``import_confirm_bytes`` (or an
unknown size) without ``confirm_large`` (``import_confirmation_required``).
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from ...clients.hf_hub import map_hf_error
from ...core.errors import AppError
from ...core.storage import data_dir
from .hashing import hash_file

logger = logging.getLogger(__name__)

BATCH_ROWS = 10_000
SPACE_FACTOR = 2.2
_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class Loader(Protocol):
    def __call__(
        self,
        path: str,
        *,
        name: str | None,
        split: str | None,
        revision: str,
        cache_dir: str,
        token: str | None,
        trust_remote_code: bool,
    ) -> Any: ...


def default_loader() -> Loader:
    import datasets

    loader: Loader = datasets.load_dataset
    return loader


@dataclass(frozen=True)
class WrittenFile:
    split: str
    path: Path
    rows: int
    bytes: int
    sha256: str
    columns: list[dict[str, str]]


def refuse_remote_code(repo_id: str, siblings: list[str]) -> None:
    scripts = [s for s in siblings if "/" not in s and s.endswith(".py")]
    if scripts:
        raise AppError(
            f"{repo_id} ships a loading script ({scripts[0]}), which would run code on this server. "
            "miDataworks does not run dataset code; use a dataset published as data files.",
            code="remote_code_refused",
            status_code=422,
            details={"scripts": scripts},
        )


def preflight(
    expected: int | None, *, confirm_large: bool, confirm_bytes: int, free: int | None = None
) -> None:
    if expected is None:
        if not confirm_large:
            raise AppError(
                "Hugging Face did not report this dataset's size, so miDataworks cannot check it fits. "
                "Import again with confirm_large if you are sure.",
                code="import_confirmation_required",
                status_code=409,
                details={"expected_bytes": None},
            )
        return
    free = shutil.disk_usage(data_dir()).free if free is None else free
    required = math.ceil(SPACE_FACTOR * expected)
    if required > free:
        raise AppError(
            f"The import needs about {required:,} bytes free (2.2x its {expected:,} bytes) and the data "
            f"volume has {free:,}. Free space first.",
            code="insufficient_space",
            status_code=507,
            details={"required_bytes": required, "free_bytes": free},
        )
    if expected > confirm_bytes and not confirm_large:
        raise AppError(
            f"This dataset is {expected:,} bytes, above the {confirm_bytes:,}-byte confirmation level. "
            "Import again with confirm_large to go ahead.",
            code="import_confirmation_required",
            status_code=409,
            details={"expected_bytes": expected, "confirm_bytes": confirm_bytes},
        )


def file_name(split: str, position: int) -> str:
    return f"{split}.parquet" if _SAFE.fullmatch(split) else f"split_{position}.parquet"


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_split(dataset: Any, destination: Path) -> WrittenFile:
    """Write one split in record batches, fsync, hash from disk, count from metadata."""
    writer: pq.ParquetWriter | None = None
    try:
        for batch in dataset.with_format("arrow").iter(batch_size=BATCH_ROWS):
            table = batch if isinstance(batch, pa.Table) else pa.Table.from_batches([batch])
            if writer is None:
                writer = pq.ParquetWriter(destination, table.schema, compression="zstd")
            writer.write_table(table)
        if writer is None:  # an empty split still gets a file with its schema
            pq.write_table(dataset.with_format("arrow")[:0], destination)
    finally:
        if writer is not None:
            writer.close()
    _fsync(destination)
    meta = pq.ParquetFile(destination)
    return WrittenFile(
        split="",
        path=destination,
        rows=meta.metadata.num_rows,
        bytes=destination.stat().st_size,
        sha256=hash_file(destination),
        columns=[{"name": f.name, "type": str(f.type)} for f in meta.schema_arrow],
    )


def map_loader_error(exc: Exception, repo_id: str, *, token_sent: bool, tier: str) -> AppError:
    """Map what ``load_dataset`` raises onto the same codes the Hub client uses (FTDD 5.4).

    The library's own message is never passed on: it can carry URLs, paths and request details.
    """
    from datasets import exceptions as ds_errors
    from huggingface_hub import errors as hf_errors

    def hf(status: int | None, why: str = "") -> AppError:
        return map_hf_error(status, "", token_sent=token_sent, repo=repo_id, tier=tier, why=why)

    if isinstance(exc, hf_errors.GatedRepoError):
        return hf(403)
    if isinstance(exc, hf_errors.RevisionNotFoundError):
        return AppError(
            f"{repo_id} no longer has the pinned commit. Import again to resolve a current one.",
            code="revision_unresolved",
            status_code=404,
        )
    if isinstance(exc, (hf_errors.RepositoryNotFoundError, ds_errors.DatasetNotFoundError)):
        return hf(401 if token_sent else 404)
    if isinstance(exc, hf_errors.HfHubHTTPError):
        response = getattr(exc, "response", None)
        return hf(getattr(response, "status_code", None))
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return hf(None, why=type(exc).__name__)
    return AppError(
        f"The dataset could not be read at the pinned commit ({type(exc).__name__}). It may use a "
        "layout the datasets library cannot load without code; the job log has the detail.",
        code="import_failed",
        status_code=502,
        details={"error_type": type(exc).__name__},
    )


def materialise(
    loader: Loader,
    *,
    repo_id: str,
    config: str | None,
    split: str | None,
    commit: str,
    cache_dir: Path,
    token: str | None,
    staging: Path,
    tier: str = "none",
) -> list[WrittenFile]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        data = loader(
            repo_id,
            name=config,
            split=split,
            revision=commit,
            cache_dir=str(cache_dir),
            token=token,
            trust_remote_code=False,
        )
    except AppError:
        raise
    except Exception as exc:  # OperatorCancelled is a BaseException and passes through untouched
        logger.warning("load_dataset failed for %s: %s", repo_id, type(exc).__name__)
        raise map_loader_error(exc, repo_id, token_sent=token is not None, tier=tier) from None
    splits: dict[str, Any] = {split: data} if split else dict(data)
    written: list[WrittenFile] = []
    for position, (name, ds) in enumerate(splits.items()):
        result = write_split(ds, staging / file_name(name, position))
        written.append(
            WrittenFile(name, result.path, result.rows, result.bytes, result.sha256, result.columns)
        )
    _fsync(staging)
    return written
