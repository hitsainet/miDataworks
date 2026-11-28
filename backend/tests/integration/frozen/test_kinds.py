"""Frozen kinds and ids (feature 020; FR-020.2): DR-01, DR-02, DR-04, DR-30, and (2026-11-26) DR-59, DR-68.

Each drives the code that reads or writes the name: the upload detector that recognises miStudio's
spot-check export, the steering-set hash miServer reproduces byte for byte, the vendored schema the
recognition is built from, and the Hub repo-id rule a publish is validated against."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from src.schemas.publishing import PublishIn
from src.services.generation import steering
from src.services.sources import spot_check
from src.services.sources.detection import detect
from tests.support.frozen import exercises

KIND = "mistudio.spot-check-disagreement/v1"
SCHEMA_ID = (
    "https://raw.githubusercontent.com/hitsainet/miStudio/main/docs/schemas/"
    "spot-check-disagreement-v1.json"
)
LONG = "a turn long enough to read as text in the detector"


def _vendored() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(spot_check.VENDORED_PATH.read_bytes())
    return loaded


def spot_row(i: int = 0, *, kind: str = KIND) -> dict[str, Any]:
    return {
        "format": kind,
        "messages": [{"role": "user", "content": LONG}, {"role": "assistant", "content": LONG}],
        "behaviour": "pii",
        "spot_check_id": f"sc_{i}",
        "session_id": f"s{i % 2}",
        "request_id": f"chatcmpl-{i}",
        "stratum": "fired",
        "probe_id": "pr_x",
        "millm_probe_id": "pr_x",
        "probe_fired": True,
        "judge_positive": False,
        "prompt_key": "k",
        "prompt_hash": "sha256:" + "0" * 64,
        "window": "all",
        "judge_saw": "same_window",
        "source": "millm-session",
    }


def _detect(rows: list[dict[str, Any]]) -> Any:
    columns = [(k, "string") for k in rows[0] if k != "messages"]
    columns.append(("messages", "list<struct<role: string, content: string>>"))
    return detect(columns, rows)


@exercises("DR-01", literals=(KIND,))
def test_dr01_spot_check_kind_imports_through_the_real_importer(literal: str) -> None:
    rows = [spot_row(i, kind=literal) for i in range(5)]
    assert _detect(rows).provenance == spot_check.PROVENANCE == "mistudio_spot_check/v1"
    validator = jsonschema.Draft202012Validator(_vendored())
    assert not list(validator.iter_errors(spot_row(kind=literal)))
    renamed = literal.replace("mistudio.", "miworkbench.")
    assert list(validator.iter_errors(spot_row(kind=renamed))), "the kind is checked by value"


@exercises("DR-02", literals=("millm.steering-set/v1",))
def test_dr02_steering_set_kind_hashes_to_the_pinned_digest(literal: str) -> None:
    """miServer recomputes this hash from the same canonical form: a renamed version line changes
    every steering hash already stored on generated rows."""
    assert steering.canonical_steering_form("gemma-2-2b/12-res-16k", [(3, 2.0)]).startswith(
        literal.encode() + b"\n"
    )
    assert steering.steering_set_hash("gemma-2-2b/12-res-16k", [(3, 2.0), (17, -1.5)]) == (
        "sha256:d09fad137a08e8e2e54fe4cf84b458aac039ad3f149932cdf0cf823f6dc222dc"
    )


@exercises("DR-04", literals=(SCHEMA_ID.removeprefix("https://"),))
def test_dr04_vendored_schema_id_is_the_one_the_importer_validates(literal: str) -> None:
    document = _vendored()
    assert document["$id"] == "https://" + literal
    assert spot_check.SPOT_CHECK_COLUMNS == spot_check.pinned_columns(document)
    assert spot_check.SPOT_CHECK_COLUMNS, "recognition is built from the vendored file"
    validator = jsonschema.Draft202012Validator(document)
    assert validator.schema["$id"] == "https://" + literal


@exercises("DR-30", literals=("mistudio/humor-test",))
def test_dr30_mistudio_account_repo_ids_are_accepted(literal: str) -> None:
    """R12 keeps the Hub account ``mistudio``: a publish to one of its repositories is valid."""
    body = PublishIn(version_id="v", build_id="b", repo_id=literal)
    assert body.repo_id == literal
    with pytest.raises(ValueError):
        PublishIn(version_id="v", build_id="b", repo_id="no-namespace")


@exercises("DR-59", literals=("millm-session",))
def test_dr59_spot_check_source_is_the_vendored_const(literal: str) -> None:
    """The vendored spot-check schema fixes ``source`` to the frozen value: a row carrying it validates,
    and the same row under a renamed value is refused, so the value is checked by value (register DR-59;
    a re-vendor that renamed it would refuse every exported disagreement file already on disk)."""
    document = _vendored()
    assert document["properties"]["source"]["const"] == literal
    validator = jsonschema.Draft202012Validator(document)
    assert not list(validator.iter_errors(dict(spot_row(), source=literal)))
    renamed = literal.replace("millm-", "miserver-")
    assert list(
        validator.iter_errors(dict(spot_row(), source=renamed))
    ), "the value is checked by value"


@exercises("DR-68")
def test_dr68_vendored_spot_check_title_is_derived_from_millm_probe_id() -> None:
    """The vendored schema's title for the DR-14 column ``millm_probe_id`` is the one pydantic derives from
    the field name, "Millm Probe Id" (register DR-68): it changes only with the field, in miWorkbench first.
    A spot-check row carrying the column validates against the same file."""
    document = _vendored()
    assert document["properties"]["millm_probe_id"]["title"] == "Millm Probe Id"
    assert "millm_probe_id" in spot_row()
    validator = jsonschema.Draft202012Validator(document)
    assert not list(validator.iter_errors(spot_row()))
