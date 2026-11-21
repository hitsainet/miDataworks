"""``hash_file`` (001 FTASKS 3.7): SHA-256 from disk in 8 MiB chunks equals ``hashlib``."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.services.sources import hashing


def test_hash_file_equals_hashlib_across_chunk_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    data = bytes(range(256)) * 70_000  # ~17.9 MB: three 8 MiB chunks, the last partial
    path.write_bytes(data)
    assert hashing.hash_file(path) == hashlib.sha256(data).hexdigest()


def test_hash_file_reads_in_8_mib_chunks() -> None:
    assert hashing.CHUNK == 8 * 1024 * 1024


def test_an_empty_file_hashes_to_the_empty_digest(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_bytes(b"")
    assert hashing.hash_file(path) == hashlib.sha256(b"").hexdigest()
