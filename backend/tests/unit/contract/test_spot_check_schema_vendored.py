"""miStudio 038's disagreement-row schema, vendored byte-identical (015 FR-015.10; ENH-005 D57; ADR-028).

miStudio owns ``docs/schemas/spot-check-disagreement-v1.json`` (``mistudio.spot-check-disagreement/v1``,
038 FR-11b). Until miStudio publishes it, nothing is vendored here, recognition is off
(``SPOT_CHECK_COLUMNS`` is empty) and these checks SKIP LOUDLY. Once miStudio has the file, this
repository must hold the same bytes in ``src/schemas/vendored/``.
"""

from __future__ import annotations

import json
import os

import pytest

from src.services.sources import spot_check
from tests.support.siblings import sibling_repo

NAME = spot_check.SCHEMA_FILE_NAME


def _require() -> bool:
    return os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1"


def test_the_vendored_copy_equals_mistudios_file() -> None:
    root = sibling_repo("miStudio", "MISTUDIO_REPO")
    theirs = root / "docs" / "schemas" / NAME
    if not theirs.exists():
        message = (
            f"CROSS-REPO: MISTUDIO_REPO: miStudio has not published docs/schemas/{NAME} under "
            f"{root} (miStudio 038 FR-11b); spot-check recognition stays off until it is vendored."
        )
        if _require() and root.exists():
            pytest.fail(message)
        pytest.skip(message)
    assert (
        spot_check.VENDORED_PATH.exists()
    ), f"miStudio publishes {NAME}: copy {theirs} to {spot_check.VENDORED_PATH} byte for byte."
    assert (
        spot_check.VENDORED_PATH.read_bytes() == theirs.read_bytes()
    ), f"{spot_check.VENDORED_PATH} differs from miStudio's {theirs}; re-vendor it."


def test_the_pinned_columns_come_from_the_vendored_file() -> None:
    if not spot_check.VENDORED_PATH.exists():
        assert spot_check.SPOT_CHECK_COLUMNS == ()
        pytest.skip(
            f"CROSS-REPO: {NAME} is not vendored yet (miStudio 038); recognition is off and "
            "SPOT_CHECK_COLUMNS is empty, as asserted."
        )
    document = json.loads(spot_check.VENDORED_PATH.read_bytes())
    assert spot_check.SPOT_CHECK_COLUMNS == spot_check.pinned_columns(document)
    assert "session_id" in spot_check.SPOT_CHECK_COLUMNS
    assert "messages" not in spot_check.SPOT_CHECK_COLUMNS


def test_pinned_columns_are_the_required_ones_minus_messages() -> None:
    doc = {"required": ["messages", "session_id", "request_id", "source"]}
    assert spot_check.pinned_columns(doc) == ("request_id", "session_id", "source")
    assert spot_check.pinned_columns(None) == ()
