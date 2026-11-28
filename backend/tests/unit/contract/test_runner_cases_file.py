"""The cases file: its own schema, unique ids, formatter idempotence, packaged == docs (FTASKS 3.6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.format_runner_cases import format_bytes
from src.runner_contract.cases import CASES_FILE_NAME, PACKAGED_CASES_PATH, load_cases_from

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / CASES_FILE_NAME


def test_the_packaged_copy_equals_the_docs_copy() -> None:
    assert PACKAGED_CASES_PATH.read_bytes() == DOCS_COPY.read_bytes()


def test_the_file_is_formatted() -> None:
    content = format_bytes(json.loads(DOCS_COPY.read_text(encoding="utf-8")))
    assert (
        DOCS_COPY.read_bytes() == content
    ), "run: cd backend && python -m scripts.format_runner_cases"


def test_the_formatter_is_idempotent() -> None:
    once = format_bytes(json.loads(DOCS_COPY.read_text(encoding="utf-8")))
    twice = format_bytes(json.loads(once.decode("ascii")))
    assert once == twice


def test_it_validates_against_its_own_case_schema() -> None:
    load_cases_from(DOCS_COPY)  # raises if invalid


def test_every_case_id_is_unique() -> None:
    cases = json.loads(DOCS_COPY.read_text(encoding="utf-8"))
    ids = [
        case["id"]
        for section in ("conformance", "hostile", "hash", "imports", "limits")
        for case in cases[section]
    ]
    assert len(ids) == len(set(ids))


def test_every_case_names_a_document_that_exists_and_an_expect() -> None:
    cases = json.loads(DOCS_COPY.read_text(encoding="utf-8"))
    docs = cases["documents"]
    for section in ("conformance", "hostile", "imports", "limits"):
        for case in cases[section]:
            assert case["doc"] in docs, case["id"]
            assert "expect" in case, case["id"]
    for case in cases["hash"]:
        assert case["document"] in docs


def test_a_case_without_a_document_is_red(tmp_path: Path) -> None:
    cases = json.loads(DOCS_COPY.read_text(encoding="utf-8"))
    cases["conformance"][0].pop("doc")
    bad = tmp_path / CASES_FILE_NAME
    bad.write_bytes(format_bytes(cases))
    with pytest.raises(Exception):  # noqa: B017 - schema rejects it
        load_cases_from(bad)


def test_no_behaviour_key_anywhere() -> None:
    """FR-018.47: no vector or document carries a behaviour key."""
    text = DOCS_COPY.read_text(encoding="utf-8")
    assert "behaviour_key" not in text
