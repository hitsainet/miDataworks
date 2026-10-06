"""The data volume: path helpers and the stage-then-rename writer (ADR-004; tasks 4.1, 4.2, 4.5).

What this module guarantees:
- Every path it returns resolves under ``DATA_DIR``. A path that would leave it — through
  ``..``, an absolute component or a symlink — raises :class:`PathOutsideDataDir`.
- A file written through :func:`atomic_write` or :func:`staged_path` is written under
  ``staging/``, fsynced, and renamed into place on the same filesystem. A reader sees the whole
  file or no file.
- Discovery helpers never list ``staging/``.
- Worker start-up clears ``tmp/`` and leaves ``staging/`` alone, because a resumable job may own
  files there.

What it refuses: identifiers that are not a single safe path segment, and any path outside
``DATA_DIR``.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from .config import get_settings

#: The directories of ADR-004, created on first use.
LAYOUT: tuple[str, ...] = (
    "sources",
    "versions",
    "runs",
    "exports",
    "publish",
    "staging",
    "cache/hf",
    "cache/embeddings",
    "tmp",
)

#: Never read by discovery or by a reader (ADR-004).
STAGING = "staging"

_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PathOutsideDataDir(ValueError):
    """A path would resolve outside ``DATA_DIR``."""


def data_dir() -> Path:
    return get_settings().data_dir


def resolve_under_data_dir(*parts: str | os.PathLike[str], root: Path | None = None) -> Path:
    """Join ``parts`` under the data directory and refuse anything that escapes it.

    Resolution follows symlinks, so a link pointing outside the volume is refused too
    (miStudio's ``resolve_user_path`` precedent).
    """
    base = (root or data_dir()).resolve()
    candidate = base.joinpath(*[os.fspath(p) for p in parts]).resolve()
    if candidate != base and base not in candidate.parents:
        raise PathOutsideDataDir(f"{candidate} is outside the data directory {base}")
    return candidate


def _segment(identifier: str) -> str:
    if not _SEGMENT.fullmatch(identifier) or identifier in {".", ".."}:
        raise PathOutsideDataDir(f"{identifier!r} is not a safe identifier for a path segment")
    return identifier


def ensure_layout(root: Path | None = None) -> None:
    for name in LAYOUT:
        resolve_under_data_dir(name, root=root).mkdir(parents=True, exist_ok=True)


def source_dir(source_id: str) -> Path:
    return resolve_under_data_dir("sources", _segment(source_id))


def version_dir(version_id: str) -> Path:
    return resolve_under_data_dir("versions", _segment(version_id))


def run_dir(job_id: str) -> Path:
    return resolve_under_data_dir("runs", _segment(job_id))


def export_dir(export_id: str) -> Path:
    return resolve_under_data_dir("exports", _segment(export_id))


def publish_dir(publish_id: str) -> Path:
    return resolve_under_data_dir("publish", _segment(publish_id))


def staging_dir() -> Path:
    return resolve_under_data_dir(STAGING)


def tmp_dir() -> Path:
    return resolve_under_data_dir("tmp")


def hf_cache_dir() -> Path:
    return resolve_under_data_dir("cache", "hf")


def embeddings_cache_dir(spec_hash: str) -> Path:
    return resolve_under_data_dir("cache", "embeddings", _segment(spec_hash))


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def staged_path(destination: Path, suffix: str = "") -> Iterator[Path]:
    """Yield a path under ``staging/``; on success fsync it and rename it to ``destination``.

    For writers that take a path (pyarrow, DuckDB). On an exception the staged file is removed
    and ``destination`` is untouched.
    """
    destination = resolve_under_data_dir(destination)
    staging = staging_dir()
    staging.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = staging / f"{uuid.uuid4().hex}{suffix or destination.suffix}"
    try:
        yield staged
        with open(staged, "rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staged, destination)  # atomic on the same filesystem
        _fsync_dir(destination.parent)
    finally:
        if staged.exists():
            staged.unlink()


def atomic_write(destination: Path, write: Callable[[BinaryIO], None]) -> Path:
    """Write through ``write(handle)`` under ``staging/`` and rename into place."""
    with staged_path(destination) as staged:
        with open(staged, "wb") as handle:
            write(handle)
            handle.flush()
    return resolve_under_data_dir(destination)


def atomic_write_bytes(destination: Path, data: bytes) -> Path:
    def _write(handle: BinaryIO) -> None:
        handle.write(data)

    return atomic_write(destination, _write)


def list_entries(*parts: str) -> list[Path]:
    """List a directory under ``DATA_DIR``, never ``staging/`` and never a staged file."""
    directory = resolve_under_data_dir(*parts)
    if directory == staging_dir() or staging_dir() in directory.parents:
        raise PathOutsideDataDir("staging/ is never listed by discovery")
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.name != STAGING)


def clean_tmp_on_worker_start() -> int:
    """Empty ``tmp/`` and leave ``staging/`` alone. Returns how many entries were removed."""
    tmp = tmp_dir()
    tmp.mkdir(parents=True, exist_ok=True)
    removed = 0
    for entry in tmp.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()
        removed += 1
    return removed
