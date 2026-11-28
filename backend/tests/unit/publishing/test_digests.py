"""Digests from the bytes on disk (FR-008.20, FR-008.21; FTASKS 5.1).

The fixture is written, hashed, then MUTATED, so the comparison has something to catch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from huggingface_hub.utils.sha import git_hash

from src.services.publishing.digests import (
    bytes_git_blob_sha1,
    file_sha256,
    git_blob_sha1,
)


@pytest.mark.parametrize("size", [0, 1, 1000, 3 * 1024 * 1024 + 7])
def test_git_blob_sha1_equals_huggingface_hubs_git_hash(tmp_path: Path, size: int) -> None:
    content = bytes((i * 31) % 256 for i in range(size))
    path = tmp_path / "f.bin"
    path.write_bytes(content)
    assert git_blob_sha1(path, chunk=1024 * 1024) == git_hash(content)
    assert bytes_git_blob_sha1(content) == git_hash(content)
    assert file_sha256(path) == hashlib.sha256(content).hexdigest()


def test_a_changed_byte_changes_both_digests(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world\n" * 1000)
    before = (git_blob_sha1(path), file_sha256(path))
    data = bytearray(path.read_bytes())
    data[5000] ^= 1
    path.write_bytes(bytes(data))
    after = (git_blob_sha1(path), file_sha256(path))
    assert before[0] != after[0] and before[1] != after[1]
