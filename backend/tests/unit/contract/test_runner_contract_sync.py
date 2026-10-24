"""The published runner contract is exactly what the package generates (FR-018.1; FTASKS 3.4).

Both copies - ``docs/schemas/`` (what miStudio 040 and miLLM 039 vendor) and ``src/schemas/data/``
(what the runner image ships) - equal ``render_contract_file()`` byte for byte, and no module in
``src/`` writes either path: ``scripts/write_runner_contract.py`` is the only writer.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from src.runner_contract.document import (
    CONTRACT_FILE_NAME,
    PACKAGED_CONTRACT_PATH,
    REGENERATE_COMMAND,
    render_contract_file,
)
from src.runner_contract.hashing import bundle_digest
from tests.unit.contract.test_rubric_schema_sync import _writes_files

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / CONTRACT_FILE_NAME
WRITER = BACKEND / "scripts" / "write_runner_contract.py"


def test_the_docs_copy_is_the_generated_one() -> None:
    assert DOCS_COPY.read_bytes() == render_contract_file(), f"regenerate: {REGENERATE_COMMAND}"


def test_the_packaged_copy_is_the_generated_one() -> None:
    assert (
        PACKAGED_CONTRACT_PATH.read_bytes() == render_contract_file()
    ), f"regenerate: {REGENERATE_COMMAND}"


def test_a_hand_edit_of_the_docs_file_is_red(tmp_path: Path) -> None:
    edited = json.loads(DOCS_COPY.read_bytes())
    edited["count_cap"] = 999
    (tmp_path / CONTRACT_FILE_NAME).write_bytes(json.dumps(edited).encode())
    assert (tmp_path / CONTRACT_FILE_NAME).read_bytes() != render_contract_file()


def test_the_bundle_digest_recomputes_from_its_parts() -> None:
    bundle = json.loads(DOCS_COPY.read_bytes())["guest"]["bundle"]
    assert bundle["bundle_digest"] == bundle_digest(bundle)


def test_the_file_carries_the_header_and_the_s0_corrections() -> None:
    doc = json.loads(DOCS_COPY.read_bytes())
    assert doc["format"] == "midataworks.runner/v1"
    assert doc["$id"].endswith("/docs/schemas/" + CONTRACT_FILE_NAME)
    assert "GENERATED" in doc["description"]
    assert doc["contract_version"] == "1.0" and doc["contract_major"] == 1
    # S0 corrections (ENH-006 D41): the bundle is a custom link + snapshot, markers are once-in-order.
    assert set(doc["guest"]["bundle"]) >= {
        "libpython",
        "wasi_sdk",
        "shim",
        "snapshot",
        "prelude_sha256",
    }
    assert doc["guest"]["bundle"]["snapshot"]["sha256"]
    fd3 = next(r for r in doc["wasi"] if "fd_write" in r["imports"])
    assert "once" in fd3["behaviour"].lower() and "order" in fd3["behaviour"].lower()
    assert {h["number"] for h in doc["hostile_classes"]} >= {17, 18, 19}


def test_no_floor_value_is_null() -> None:
    doc = json.loads(DOCS_COPY.read_bytes())
    assert all(v is not None for v in doc["limits_floor"].values())


def test_the_prelude_and_contract_static_refusals_agree() -> None:
    """The refusal set lives twice: as ``constants.STATIC_REFUSALS`` (published) and inside the
    guest prelude source as ``_REFUSED_NAMES``. They must match, so the published contract describes
    what the guest actually refuses."""
    from src.runner_contract import constants
    from src.runner_contract.prelude import PRELUDE_SOURCE

    tree = ast.parse(PRELUDE_SOURCE)
    refused: set[str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_REFUSED_NAMES" for t in node.targets
        ):
            value = node.value
            if isinstance(value, ast.Call):  # frozenset((...))
                value = value.args[0]
            refused = set(ast.literal_eval(value))
    assert refused is not None, "the prelude must define _REFUSED_NAMES"
    assert refused == set(constants.STATIC_REFUSALS)


def _names_the_file(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "CONTRACT_FILE_NAME",
            "PACKAGED_CONTRACT_PATH",
        }:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and CONTRACT_FILE_NAME in node.value
        ):
            return True
    return False


def test_only_the_script_writes_the_contract_file() -> None:
    offenders = [
        str(p.relative_to(BACKEND))
        for p in sorted((BACKEND / "src").rglob("*.py"))
        if _names_the_file(t := ast.parse(p.read_text())) and _writes_files(t)
    ]
    assert offenders == []


def test_the_writer_guard_sees_the_script() -> None:
    tree = ast.parse(WRITER.read_text())
    assert _names_the_file(tree) and _writes_files(tree)
