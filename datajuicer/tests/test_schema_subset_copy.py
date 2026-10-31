"""The image's schema_subset.py is a byte copy of the backend module (FTID 003 I-3)."""

from __future__ import annotations

import dj_paths


def test_schema_subset_is_a_byte_copy() -> None:
    backend = dj_paths.BACKEND / "src" / "operators" / "schema_subset.py"
    assert (dj_paths.ROOT / "schema_subset.py").read_bytes() == backend.read_bytes()
