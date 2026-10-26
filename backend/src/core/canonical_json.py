"""The one canonical-JSON function (ADR-005; Foundation task 4.3).

Hashing and writing both call :func:`canonical_json`. There is no second serialiser: miStudio's
033 arc shipped an integrity pin computed from a different serialisation than the file it pinned
(an indented writer, a compact digest), and every published probe would have told its first
reader the file was corrupt.

Canonical form: keys sorted, no insignificant whitespace (``,`` and ``:`` separators), UTF-8,
non-ASCII characters written as themselves rather than ``\\u`` escapes, and NaN or infinity
refused (they are not JSON).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Serialise ``value`` to canonical JSON bytes."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Hex SHA-256 of the canonical JSON of ``value``: the recipe hash and row key primitive."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def write_canonical_json(handle: Any, value: Any) -> None:
    """Write the canonical bytes to a binary handle. The bytes written are the bytes hashed."""
    handle.write(canonical_json(value))
