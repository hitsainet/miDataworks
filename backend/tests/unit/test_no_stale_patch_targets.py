"""A module named by a STRING still exists (feature 020 P4, FR-020.22; FTASKS 15.7, control M33).

P4 moves five modules with ``git mv`` and no compatibility module (TPD-13):

    src/clients/mistudio_client.py      -> src/clients/miworkbench_client.py
    src/clients/millm_lease_client.py   -> src/clients/miserver_lease_client.py
    src/clients/millm_batch_client.py   -> src/clients/miserver_batch_client.py
    tests/support/fake_mistudio.py      -> tests/support/fake_miworkbench.py
    tests/support/fake_millm.py         -> tests/support/fake_miserver.py

An ``import`` of a moved module fails loudly. A module named by a string does not: a
``monkeypatch.setattr("src.clients.x.TRANSPORT", ...)`` raises only when its test runs (and reads
as that test's failure, not as a stale name), a logger name or an AST guard comparing
``node.module`` to a string silently matches nothing. So:

1. every dotted ``src.`` / ``tests.`` string in the test tree resolves to a module or to an
   attribute of one (green before the move, and what turns red on a stale target after it);
2. no source or test file names an old module stem as a token (red before the move);
3. the old files are gone and the new ones exist, each a real module, not a re-export.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
DOTTED = re.compile(r"(src|tests)(\.[A-Za-z_][A-Za-z0-9_]*)+")
MOVED = {
    "src/clients/mistudio_client.py": "src/clients/miworkbench_client.py",
    "src/clients/millm_lease_client.py": "src/clients/miserver_lease_client.py",
    "src/clients/millm_batch_client.py": "src/clients/miserver_batch_client.py",
    "tests/support/fake_mistudio.py": "tests/support/fake_miworkbench.py",
    "tests/support/fake_millm.py": "tests/support/fake_miserver.py",
}
#: ``"src.parquet"`` is a file name in an upload test, not a module.
FILE_SUFFIXES = {"parquet", "json", "jsonl", "csv", "txt", "py"}
OLD_STEMS = re.compile(r"\b(" + "|".join(Path(old).stem for old in MOVED) + r")\b")


def python_files(*roots: str) -> list[Path]:
    return sorted(
        path
        for root in roots
        for path in (BACKEND / root).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def dotted_strings(paths: list[Path]) -> list[tuple[str, str]]:
    found = []
    for path in paths:
        if path.resolve() == Path(__file__).resolve():
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if (
                    DOTTED.fullmatch(node.value)
                    and node.value.rsplit(".", 1)[1] not in FILE_SUFFIXES
                ):
                    found.append((f"{path.relative_to(BACKEND)}:{node.lineno}", node.value))
    return found


def resolves(target: str) -> bool:
    """``a.b.c`` is a module, or an attribute chain on the longest importable prefix."""
    parts = target.split(".")
    for cut in range(len(parts), 0, -1):
        name = ".".join(parts[:cut])
        try:
            found: object = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            # Only "this very name is not a module" means "try a shorter prefix"; a module that
            # exists and fails on one of ITS imports is a real failure.
            if exc.name is None or not name.startswith(exc.name):
                raise
            continue
        for attribute in parts[cut:]:
            if not hasattr(found, attribute):
                return False
            found = getattr(found, attribute)
        return True
    return False


def test_no_string_patch_target_names_a_moved_module() -> None:
    targets = dotted_strings(python_files("tests"))
    assert len(targets) >= 60, "the scan found too few dotted module strings to mean anything"
    stale = [f"{where}: {target}" for where, target in targets if not resolves(target)]
    assert stale == [], stale


def test_the_resolver_tells_a_stale_target_from_a_live_one() -> None:
    assert resolves("src.core.sibling_origins")
    assert resolves("src.core.sibling_origins.call_base")
    assert not resolves("src.core.sibling_origins.no_such_function")
    assert not resolves("src.clients.no_such_client.TRANSPORT")


def test_no_name_of_a_moved_module_remains() -> None:
    """As a token anywhere in a source or test file: an import, a string an AST guard compares
    with, a path built from parts, a docstring that would send a reader to a file that is gone."""
    stale = []
    for path in python_files("src", "tests"):
        if path.resolve() == Path(__file__).resolve():
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if OLD_STEMS.search(line):
                stale.append(f"{path.relative_to(BACKEND)}:{number}: {line.strip()[:100]}")
    assert stale == [], stale[:15]


def test_the_five_modules_moved_and_left_nothing_behind() -> None:
    for old, new in MOVED.items():
        assert not (BACKEND / old).exists(), f"{old} still exists (no compatibility module, TPD-13)"
        assert (BACKEND / new).is_file(), new
        module = importlib.import_module(new.removesuffix(".py").replace("/", "."))
        assert Path(module.__file__ or "").resolve() == (BACKEND / new).resolve()


def test_the_public_classes_follow_the_modules_and_the_wire_codes_do_not() -> None:
    """FR-020.22: classes are renamed; what crosses the wire is not (register DR-18)."""
    client = importlib.import_module("src.clients.miworkbench_client")
    for name in (
        "MiWorkbenchClient",
        "MiWorkbenchError",
        "MiWorkbenchUnreachable",
        "MiWorkbenchTimeout",
        "MiWorkbenchNotJson",
        "MiWorkbenchDatasetExists",
        "MiWorkbenchRefused",
        "MiWorkbenchPositionsNotRecorded",
        "MiWorkbenchScoresRefused",
    ):
        assert hasattr(client, name), name
    assert not [n for n in vars(client) if n.startswith("MiStudio")]
    assert client.MiWorkbenchUnreachable.code == "mistudio_unreachable"
    assert client.MiWorkbenchNotJson.code == "mistudio_not_json"
    assert client.MiWorkbenchDatasetExists.code == "mistudio_dataset_exists"
    from src.models.detector_send import MiStudioRegistration

    assert MiStudioRegistration.__tablename__ == "dw_mistudio_registrations"
