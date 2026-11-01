"""``rule-signal-cases-v1.json`` is well formed (FR-016.6; FTASKS 3.1, 3.2).

It validates against its own ``case_schema``; every case id is unique; the bytes are exactly the
formatter's (sorted keys, two-space indent, ASCII escapes, one trailing newline); the packaged copy
equals the docs copy; every case carries a ``source``; every document is a rule-set document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from scripts.format_rule_signal_cases import DOCS_PATH, format_bytes
from src.rules.cases import CASES_FORMAT, PACKAGED_CASES_PATH
from src.rules.rule_set import parse

PRIVATE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "private" / "rule-signal-private-cases.json"
)
SECTION_DEF = {
    "validators": "validator_case",
    "refusals": "refusal_case",
    "renderings": "rendering_case",
    "rule_set_identity": "identity_case",
}


def _file() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(DOCS_PATH.read_text(encoding="utf-8"))
    return data


def all_cases(data: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(section, $def, case) for every case in the file."""
    out = [
        (f"validators/{ref}", "validator_case", c)
        for ref, sec in data["validators"].items()
        for c in sec["cases"]
    ]
    for kind, cases in data["rule_kinds"].items():
        out += [(f"rule_kinds/{kind}", SECTION_DEF.get(kind, "rule_case"), c) for c in cases]
    out += [("rule_set_identity", "identity_case", c) for c in data["rule_set_identity"]]
    return out


def schema_errors(data: dict[str, Any]) -> list[str]:
    defs = data["case_schema"]["$defs"]
    errors = []
    for section, name, case in all_cases(data):
        schema = {"$defs": defs, "$ref": f"#/$defs/{name}"}
        for e in jsonschema.Draft202012Validator(schema).iter_errors(case):
            errors.append(f"{section}/{case.get('id')}: {e.message}")
    return errors


def test_the_header() -> None:
    data = _file()
    assert data["format"] == CASES_FORMAT
    assert "code points" in data["units"]
    assert "truth" in data["precedence"]


def test_every_case_validates_against_the_case_schema() -> None:
    assert schema_errors(_file()) == []


def test_a_case_without_a_source_is_refused() -> None:
    data = _file()
    del data["validators"]["luhn@1"]["cases"][0]["source"]
    assert any("'source' is a required property" in e for e in schema_errors(data))


def test_case_ids_are_unique() -> None:
    ids = [c["id"] for _, _, c in all_cases(_file())]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_the_bytes_are_the_formatters() -> None:
    raw = DOCS_PATH.read_bytes()
    assert raw == format_bytes(
        json.loads(raw)
    ), "run: cd backend && python -m scripts.format_rule_signal_cases"
    assert raw.isascii() and raw.endswith(b"}\n") and not raw.endswith(b"\n\n")


def test_the_packaged_copy_equals_the_docs_copy() -> None:
    assert PACKAGED_CASES_PATH.read_bytes() == DOCS_PATH.read_bytes()


def test_every_document_is_a_rule_set_document() -> None:
    data = _file()
    for doc in data["rule_documents"].values():
        parse(doc)
    for _, _, case in all_cases(data):
        if isinstance(case.get("document"), str):
            assert case["document"] in data["rule_documents"], case["id"]


def test_the_private_cases_are_well_formed() -> None:
    if not PRIVATE.exists():
        pytest.skip("MIRROR: backend/tests/fixtures/private/ is stripped from the public mirror")
    private = json.loads(PRIVATE.read_text(encoding="utf-8"))
    defs = _file()["case_schema"]["$defs"]
    schema = {"$defs": defs, "$ref": "#/$defs/validator_case"}
    for sec in private["validators"].values():
        for case in sec["cases"]:
            assert not list(jsonschema.Draft202012Validator(schema).iter_errors(case)), case["id"]
