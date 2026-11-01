"""The committed Data Designer catalogue is exactly what the builder produces (FTASKS 8.2)."""

from __future__ import annotations

import sys

import dd_paths

sys.path.insert(0, str(dd_paths.ROOT / "scripts"))

import build_catalogue  # noqa: E402


def test_catalogue_is_byte_identical_to_a_fresh_build() -> None:
    assert build_catalogue.OUTPUT.read_bytes() == build_catalogue.render(build_catalogue.build())
