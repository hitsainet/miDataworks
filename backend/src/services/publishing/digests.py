"""File digests for publishing (FR-008.20, FR-008.21; FTID 008 sections 7.1, 11).

Two hashes per file, both streamed from the bytes on disk, never from a re-serialisation:

- **SHA-256** of the file's bytes: what the Hub reports as ``lfs.sha256`` for a large file, and
  what the card and the manifest print. It is :func:`identity.file_sha256` — one implementation.
- **git blob SHA-1**, ``sha1(b"blob <size>\\0" + content)``: what the Hub reports as ``blob_id``
  for a small, non-LFS file (the card, the manifest). Equal to
  ``huggingface_hub.utils.sha.git_hash`` on the same bytes, asserted on fixtures including a
  multi-chunk file. Skipping small files is not allowed: miStudio's defect was in a JSON file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..identity import bytes_sha256, file_sha256

CHUNK = 8 * 1024 * 1024

__all__ = ["CHUNK", "bytes_git_blob_sha1", "bytes_sha256", "file_sha256", "git_blob_sha1"]


def git_blob_sha1(path: Path, chunk: int = CHUNK) -> str:
    """The git blob id of a file, streamed (``git hash-object``)."""
    size = path.stat().st_size
    digest = hashlib.sha1(f"blob {size}\0".encode(), usedforsecurity=False)
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def bytes_git_blob_sha1(content: bytes) -> str:
    digest = hashlib.sha1(f"blob {len(content)}\0".encode(), usedforsecurity=False)
    digest.update(content)
    return digest.hexdigest()
