"""miAssist's ``mistudio.result-language/v1``, vendored byte-identical (022 FR-022.1; ADR-028 as amended).

miAssist owns ``docs/schemas/mistudio-result-language-v1.json``; this repository holds the same bytes
at the same path and reads them in tests only (no runtime path depends on it, FR-022.25). With no
miAssist checkout this SKIPS LOUDLY; under ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` or
``MISTUDIO_REQUIRE_CROSS_REPO_CHECKS=1`` an absent sibling FAILS, whether or not its folder exists
(``tests/support/siblings.absent``).
"""

from __future__ import annotations

import pytest

from tests.support.result_language import SCHEMA_NAME, VENDORED, theirs_path
from tests.support.siblings import absent


def test_the_vendored_copy_equals_miassists_file() -> None:
    theirs = theirs_path()
    if not theirs.exists():
        absent(
            f"MIASSIST_REPO: no miAssist docs/schemas/{SCHEMA_NAME} at {theirs}; the "
            "byte-identity of the vendored result language is unchecked."
        )
    assert VENDORED.exists(), f"copy {theirs} to {VENDORED} byte for byte"
    assert (
        VENDORED.read_bytes() == theirs.read_bytes()
    ), f"{VENDORED} differs from miAssist's {theirs}: re-vendor it with cp (miAssist pushes first)"


def test_the_vendored_copy_is_present_and_names_its_format() -> None:
    import json

    document = json.loads(VENDORED.read_text(encoding="utf-8"))
    assert "result-language" in document["$id"]
    for name in ("approval", "approval_status", "next_step", "unavailable", "error_code"):
        assert name in document["$defs"], name


def test_the_helper_refuses_a_bad_approval() -> None:
    """FR-022.2: shape tests read the vendored file, so a malformed approval fails there."""
    import jsonschema

    from tests.support.result_language import validate

    good = {
        "id": "apr_1",
        "status": "pending",
        "decide_in": "midataworks",
        "then": "server_runs",
        "summary": "Publish ver_1 to the Hub",
    }
    validate("approval", good)
    for bad in (
        {**good, "status": "approved_by_magic"},
        {**good, "decide_in": "miassist"},
        {k: v for k, v in good.items() if k != "summary"},
        {**good, "extra": 1},
    ):
        with pytest.raises(jsonschema.ValidationError):
            validate("approval", bad)
