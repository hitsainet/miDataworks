"""The one file hasher of feature 001 (FR-001.3, FR-001.23; 001 FTASKS 3.7): SHA-256 read from
disk in 8 MiB chunks.

"Hash what was written": every stored file is hashed after it is fsynced, from the bytes on disk,
never from memory (001 FTDD section 1, principle 2). Delegates to ``services/identity.py``, the
module the single-call-site guard allows to hash.
"""

from __future__ import annotations

from pathlib import Path

from ..identity import file_sha256

CHUNK = 8 * 1024 * 1024


def hash_file(path: Path) -> str:
    return file_sha256(path, chunk=CHUNK)
