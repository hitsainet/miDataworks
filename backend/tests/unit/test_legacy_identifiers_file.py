"""The rebrand export and the frozen-identifier registry (feature 020; FR-020.1 to FR-020.3; CF-1;
FTASKS 1.1, 2.1).

Part 1 proves the LOADER refuses what it does not understand (synthetic files, so each refusal is
pinned on its own). Part 2 reads the COMMITTED export and the live ``@exercises`` registry and
requires them to agree both ways: every register entry this repository carries has a behaviour
test that exists and is registered for it, and no registered test claims an entry the export does
not list (controls M1, M1b, M1c).
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import subprocess
from typing import Any

import pytest

from tests.support import frozen
from tests.support import legacy_identifiers as li

REPO = li.REPO
FROZEN_DIR = REPO / "backend" / "tests" / "integration" / "frozen"

HEADER = {
    "format": 1,
    "repo": "miDataworks",
    "variant": "full",
    "register_hash": "sha256:" + "0" * 64,
    "phase": "P0",
    "closed": None,
    "withheld": [],
}
ENTRY = {
    "section": "entry",
    "id": "DR-99",
    "kind": "API field names",
    "group": "C",
    "status": "frozen",
    "retire_phase": None,
    "pattern": "(?i)\\bx-millm-[a-z-]+",
    "pattern_js": "\\bx-millm-[a-z-]+",
    "js_flags": "i",
    "literals": ["X-miLLM-Strict"],
    "exercise": "pytest:backend/tests/integration/frozen/test_headers.py::test_x",
    "flags": [],
}


def text_of(header: dict[str, Any] | None = None, *records: dict[str, Any]) -> str:
    lines = [json.dumps(header if header is not None else HEADER)]
    lines += [json.dumps(r) for r in (records or (ENTRY,))]
    return "# preface\n\n```jsonl\n" + "\n".join(lines) + "\n```\n"


def refused(text: str, *needles: str) -> None:
    with pytest.raises(li.ExportError) as raised:
        li.parse(text)
    for needle in needles:
        assert needle in str(raised.value), str(raised.value)


# --- part 1: the loader (FTASKS 1.1) ----------------------------------------------------------


def test_the_block_parses_with_format_1() -> None:
    export = li.parse(text_of())
    assert export.header["format"] == 1 and export.entry("DR-99") is not None
    refused(text_of({**HEADER, "format": 2}), "format 2")


def test_unknown_section_key_or_flag_is_refused() -> None:
    refused(text_of(HEADER, {"section": "hosts", "old": "a", "new": "b"}), "unknown section")
    refused(text_of(HEADER, {**ENTRY, "locations": []}), "unknown key", "locations")
    refused(text_of(HEADER, {**ENTRY, "flags": ["i"]}), "flags")
    refused(text_of({**HEADER, "register_commit": "abc"}), "header", "register_commit")


def test_header_requires_closed() -> None:
    header = {k: v for k, v in HEADER.items() if k != "closed"}
    refused(text_of(header), "header", "closed")


def test_host_records_require_boolean_observed() -> None:
    host = {"section": "host", "old": "a.local", "new": "b.local", "status": "transition"}
    refused(text_of(HEADER, host), "missing", "observed")
    refused(text_of(HEADER, {**host, "observed": "yes"}), "observed")
    assert li.parse(text_of(HEADER, {**host, "observed": False})).section("host")


def test_guard_effect_and_phase_names_are_closed_sets() -> None:
    guard = {
        "section": "guard",
        "id": "G-X",
        "pattern": "x",
        "pattern_js": "x",
        "js_flags": "",
        "effect": "deny",
        "from_phase": None,
        "until_phase": None,
    }
    assert li.parse(text_of(HEADER, guard))
    refused(text_of(HEADER, {**guard, "effect": "warn"}), "effect")
    refused(text_of(HEADER, {**guard, "from_phase": "P9"}), "from_phase")
    refused(text_of({**HEADER, "phase": "p0"}), "phase")
    refused(text_of(HEADER, {**ENTRY, "status": "deferred"}), "status")


def test_js_flags_are_empty_or_i() -> None:
    refused(text_of(HEADER, {**ENTRY, "js_flags": "g"}), "js_flags")
    assert li.parse(text_of(HEADER, {**ENTRY, "js_flags": ""}))


def test_inline_flag_refusal_allows_lookarounds() -> None:
    refused(text_of(HEADER, {**ENTRY, "pattern_js": "(?i:x-millm-strict)"}), "inline flag")
    refused(text_of(HEADER, {**ENTRY, "pattern_js": "(?s)x-millm-strict"}), "inline flag")
    for legal in ("(?![-\\w])x-millm-strict", "(?:x-millm-strict|x-millm-seed)", "(?<=a)b"):
        assert li.parse(text_of(HEADER, {**ENTRY, "pattern_js": legal}))


def test_forbidden_constructs_are_refused() -> None:
    for construct in ("\\Ax-millm-strict", "x-millm-strict\\Z", "(?P<n>x-millm-strict)", "(?#c)x"):
        refused(text_of(HEADER, {**ENTRY, "pattern": construct, "literals": []}), "JavaScript")


def test_literals_match_pattern() -> None:
    refused(text_of(HEADER, {**ENTRY, "literals": ["X-miServer-Strict"]}), "X-miServer-Strict")


def test_the_committed_export_parses() -> None:
    export = li.load()
    assert export.header["repo"] == "miDataworks"
    assert export.entries(), "the export lists no entry"


# --- part 2: the committed export against the live registry (FTASKS 2.1) ---------------------


def _frozen_modules() -> list[str]:
    files = sorted(p.stem for p in FROZEN_DIR.glob("test_*.py"))
    assert files, f"no frozen-identifier module under {FROZEN_DIR}"
    return files


def _import_frozen() -> dict[str, list[tuple[str, str, tuple[str, ...]]]]:
    """Import every frozen module BY NAME and fail closed if one was not reached."""
    for stem in _frozen_modules():
        importlib.import_module(f"tests.integration.frozen.{stem}")
    reached = {
        module.rsplit(".", 1)[-1] for rows in frozen.REGISTRY.values() for module, *_ in rows
    }
    missing = set(_frozen_modules()) - reached
    assert not missing, f"frozen modules that registered nothing: {sorted(missing)}"
    return frozen.REGISTRY


def _carried(export: li.Export) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in export.entries() if "history_only" not in e["flags"]}


def test_frozen_modules_are_all_imported() -> None:
    registry = _import_frozen()
    assert registry


def test_records_equal_the_exercise_registry_both_ways() -> None:
    export = li.load()
    registry = _import_frozen()
    carried = set(_carried(export))
    if export.variant == "public":
        carried |= {wid for wid in export.withheld() if wid.startswith("DR-")}
    assert set(registry) == carried, (
        f"registered, not in the export: {sorted(set(registry) - carried)}; "
        f"in the export, no registered test: {sorted(carried - set(registry))}"
    )


def test_each_exercise_resolves_to_a_registered_test() -> None:
    export = li.load()
    registry = _import_frozen()
    for entry_id, entry in _carried(export).items():
        exercise = entry["exercise"]
        if exercise is None:
            continue  # test_no_record_is_pending reports it
        match = li.EXERCISE.match(exercise)
        assert match and match.group("py"), f"{entry_id}: {exercise!r} is not a pytest id"
        path = REPO / match.group("py")
        assert path.is_file(), f"{entry_id}: {path} does not exist"
        function = match.group("pyfn").split("[", 1)[0]
        tree = ast.parse(path.read_text())
        names = {
            n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert function in names, f"{entry_id}: {function} is not defined in {path.name}"
        module = "tests." + ".".join(
            path.relative_to(REPO / "backend" / "tests").with_suffix("").parts
        )
        assert (module, function) in {
            (m, f) for m, f, _ in registry.get(entry_id, [])
        }, f"{entry_id}: {function} is not registered for it with @exercises"


def test_no_record_is_pending() -> None:
    export = li.load()
    for entry in export.entries():
        assert "pending" not in entry["flags"], f"{entry['id']} is pending (CF-1 bootstrap)"
        if entry["exercise"] is None:
            assert {"history_only", "text_only"} & set(
                entry["flags"]
            ), f"{entry['id']} has no exercise and is neither history_only nor text_only"


def test_literal_coverage_of_frozen_tests() -> None:
    """CF-28: each record's literals (census writes this repository's subset) are declared by the
    tests registered for it; declaring a literal forces exercising it (frozen.exercises)."""
    export = li.load()
    registry = _import_frozen()
    for entry_id, entry in _carried(export).items():
        declared = {lit for _, _, lits in registry.get(entry_id, []) for lit in lits}
        missing = set(entry["literals"]) - declared
        assert not missing, f"{entry_id}: literals no registered test declares: {sorted(missing)}"


def test_history_only_patterns_match_nothing_outside_history() -> None:
    export = li.load()
    history = [e for e in export.entries() if "history_only" in e["flags"]]
    files = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--",
            ".",
            ":(exclude)0xcc/**",
            ":(exclude)AGENT.md",
            ":(exclude,glob)**/docs/legacy-identifiers*.md",
        ],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    texts = []
    for raw in filter(None, files):
        path = REPO / raw.decode()
        if path.is_file() and path.suffix not in {".parquet", ".png", ".ico", ".woff2"}:
            try:
                texts.append((raw.decode(), path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    assert len(texts) > 100, "the tracked tree was not read"
    for entry in history:
        pattern = re.compile(entry["pattern"])
        hits = [rel for rel, text in texts if pattern.search(text)]
        assert not hits, f"{entry['id']} (history_only) matches {hits[:5]}"
