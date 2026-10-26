"""No app or infrastructure code names a behaviour (FR-016.33, FR-016.34; FTASKS 5.2, 5.3, 5.5 - 5.7).

Reads TOKENS, never lines: Python through ``tokenize`` (NAME and STRING tokens, docstrings included,
comments excluded) over ``backend/src``, ``backend/scripts`` and the root ``scripts``; YAML keys and
scalars under ``k8s/``; ``shlex`` words of shell scripts. Names come from behaviour data only
(``names.json``, written by ``scripts/write_behaviour_names.py`` and pinned to it here). Matching is
case-insensitive on words: identifiers split on ``_``, ``-`` and camelCase, strings into words; a
multi-word name matches as a contiguous sequence inside one token.

Exempt: inside ``src/rules/validators/``, the ``x-hint`` strings of operator parameters and the
``identifiers`` / ``rule_signals`` howto topics, a token EXACTLY equal to a registered validator id
(a format may be named there; a behaviour key never is). Remaining hits must be listed in
``name_debt.json`` (data, not a code exemption); a listed entry whose token no longer occurs in its
file is stale and red. The guard fails closed: an empty or short name source is red.
"""

from __future__ import annotations

import ast
import io
import json
import re
import shlex
import tokenize
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.write_behaviour_names import NAMES_PATH, collect, render
from src.rules.validators import VALIDATORS
from tests.support.source_tree import stripped_by_mirror

REPO = Path(__file__).resolve().parents[3]
DEBT_PATH = REPO / "backend" / "tests" / "fixtures" / "behaviours" / "name_debt.json"
VALIDATORS_DIR = "backend/src/rules/validators/"
HOWTO_FILE = "backend/src/mcp_server/tools/core.py"
EXEMPT_HOWTO_TOPICS = ("identifiers", "rule_signals")

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def words(text: str) -> list[str]:
    out: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9]+", text):
        out += [w.lower() for w in _CAMEL.findall(chunk)]
    return out


def contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


# --- the name source -------------------------------------------------------------------------------


def load_names() -> dict[str, list[str]]:
    """name -> its word sequence, from names.json; fails closed."""
    data = json.loads(NAMES_PATH.read_text(encoding="utf-8"))
    names = list(data["names"])
    declared = {b["behaviour_key"] for b in data["behaviours"]} | {
        t for b in data["behaviours"] for t in b["vocabulary"]
    }
    if not names:
        raise AssertionError("the name source is empty: the guard would check nothing")
    if len(names) < len(declared) or data["name_count"] != len(names):
        raise AssertionError(
            f"the name source holds fewer names ({len(names)}) than declared ({len(declared)})"
        )
    return {name: words(name) for name in names}


def behaviour_keys() -> set[str]:
    return {
        b["behaviour_key"] for b in json.loads(NAMES_PATH.read_text(encoding="utf-8"))["behaviours"]
    }


# --- tokens per file ------------------------------------------------------------------------------


Token = tuple[int, str, bool]  # (line, text, exempt-context)


def _howto_lines(path: Path) -> set[int]:
    """Lines of the exempt howto topics' text (the HOWTO dict values under the exempt keys)."""
    lines: set[int] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in EXEMPT_HOWTO_TOPICS:
                    lines |= set(range(value.lineno, (value.end_lineno or value.lineno) + 1))
    return lines


def python_tokens(path: Path, rel: str) -> Iterator[Token]:
    howto = _howto_lines(path) if rel == HOWTO_FILE else set()
    in_validators = rel.startswith(VALIDATORS_DIR)
    previous: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if tok.type not in (tokenize.NAME, tokenize.STRING, tokenize.FSTRING_MIDDLE, tokenize.OP):
            continue
        if tok.type == tokenize.OP:
            previous = [*previous[-1:], tok.string]
            continue
        hint = previous[-2:] == ['"x-hint"', ":"] if len(previous) >= 2 else False
        exempt = in_validators or tok.start[0] in howto or hint
        text = tok.string
        if tok.type == tokenize.STRING:
            try:
                text = str(ast.literal_eval(tok.string))
            except (ValueError, SyntaxError):
                pass
        yield tok.start[0], text, exempt
        previous = [*previous[-1:], tok.string]


def _yaml_scalars(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _yaml_scalars(v)
    elif isinstance(node, list):
        for item in node:
            yield from _yaml_scalars(item)
    elif node is not None:
        yield str(node)


def yaml_tokens(path: Path) -> Iterator[Token]:
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        for text in _yaml_scalars(doc):
            yield 0, text, False


def shell_tokens(path: Path) -> Iterator[Token]:
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            parts = line.split()
        for word in parts:
            yield n, word, False


def scanned_files(root: Path = REPO) -> Iterator[tuple[str, Path]]:
    groups: Iterable[tuple[str, str]] = (
        ("backend/src", "**/*.py"),
        ("backend/scripts", "**/*.py"),
        ("backend/scripts", "**/*.sh"),
        ("scripts", "**/*.py"),
        ("scripts", "**/*.sh"),
        ("k8s", "**/*.yaml"),
        ("k8s", "**/*.yml"),
    )
    for base, pattern in groups:
        for path in sorted((root / base).glob(pattern)):
            if "__pycache__" not in path.parts:
                yield path.relative_to(root).as_posix(), path


def tokens_of(rel: str, path: Path) -> Iterator[Token]:
    if path.suffix == ".py":
        return python_tokens(path, rel)
    if path.suffix in (".yaml", ".yml"):
        return yaml_tokens(path)
    return shell_tokens(path)


# --- the guard -------------------------------------------------------------------------------------


def hits(names: dict[str, list[str]], root: Path = REPO) -> list[tuple[str, int, str, str]]:
    """(path, line, name, token text) for every token naming a behaviour."""
    validator_ids = {i for i, _ in VALIDATORS}
    keys = behaviour_keys()
    found = []
    for rel, path in scanned_files(root):
        for line, text, exempt in tokens_of(rel, path):
            if exempt and text.strip() in validator_ids and text.strip() not in keys:
                continue
            token_words = words(text)
            for name, seq in names.items():
                if contains(token_words, seq):
                    found.append((rel, line, name, text[:80]))
    return found


def load_debt() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = json.loads(DEBT_PATH.read_text(encoding="utf-8"))["entries"]
    for e in entries:
        assert set(e) == {"path", "token", "owner", "reason"}, e
    return entries


def stale_debt(
    entries: list[dict[str, str]], root: Path = REPO, unchecked: list[str] | None = None
) -> list[dict[str, str]]:
    """Entries whose token no longer occurs in their file. An entry whose file is ABSENT because
    the public mirror strips it (``sync-to-clean.yml``) is not stale: it goes to ``unchecked``
    (Review R1: the mirror strips the root ``scripts/`` the debt lists). An absent file the mirror
    does not strip is stale."""
    stale = []
    for e in entries:
        path = root / e["path"]
        seq = words(e["token"])
        if not path.exists() and stripped_by_mirror(e["path"]):
            if unchecked is not None:
                unchecked.append(e["path"])
            continue
        if not path.exists() or not any(
            contains(words(t), seq) for _, t, _ in tokens_of(e["path"], path)
        ):
            stale.append(e)
    return stale


def unlisted(found: list[tuple[str, int, str, str]], entries: list[dict[str, str]]) -> list[Any]:
    listed = {(e["path"], e["token"].lower()) for e in entries}
    return [h for h in found if (h[0], h[2].lower()) not in listed]


# --- tests -----------------------------------------------------------------------------------------


def test_names_json_is_what_the_behaviour_data_declares() -> None:
    assert NAMES_PATH.read_bytes() == render(
        collect()
    ), "run: cd backend && python -m scripts.write_behaviour_names"


def test_no_shipped_code_names_a_behaviour() -> None:
    found = unlisted(hits(load_names()), load_debt())
    assert (
        found == []
    ), "behaviour names in shipped code (reword, or list as debt with an owner):\n" + "\n".join(
        f"{p}:{n} {name!r} in {t!r}" for p, n, name, t in found
    )


def test_no_debt_entry_is_stale() -> None:
    unchecked: list[str] = []
    assert stale_debt(load_debt(), unchecked=unchecked) == []
    if unchecked:
        pytest.skip(
            "SOURCE-ONLY: debt entries in files the public mirror strips were not checked: "
            + ", ".join(sorted(set(unchecked)))
        )


def test_the_scan_reads_tokens_from_every_area() -> None:
    """Not vacuous: each area yields files and tokens."""
    files = list(scanned_files())
    for base in ("backend/src/", "backend/scripts/", "scripts/", "k8s/"):
        if not (REPO / base).exists() and stripped_by_mirror(base):
            continue  # the public mirror strips the root scripts/ (Review R1)
        assert any(rel.startswith(base) for rel, _ in files), base
    rel, path = next((r, p) for r, p in files if r == "backend/src/rules/validators/formats.py")
    assert any(t == "card_pan" and exempt for _, t, exempt in tokens_of(rel, path))


def test_comments_are_not_read_but_docstrings_are(tmp_path: Path) -> None:
    sample = tmp_path / "m.py"
    sample.write_text('"""Docstring about quorbish."""\n# a comment about zantimere\nx = 1\n')
    texts = [t for _, t, _ in python_tokens(sample, "backend/src/m.py")]
    assert any("quorbish" in t for t in texts) and not any("zantimere" in t for t in texts)


# --- the four negative controls, each in-process (FTASKS 5.7; also run as file edits) ---------------


def _tree(tmp_path: Path, rel: str, content: str) -> Path:
    for base in ("backend/src", "backend/scripts", "scripts", "k8s"):
        (tmp_path / base).mkdir(parents=True, exist_ok=True)
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return tmp_path


def test_control_1_a_key_in_a_string_literal_of_a_non_exempt_module(tmp_path: Path) -> None:
    root = _tree(tmp_path, "backend/src/services/x.py", 'LABEL = "Fixture-Alpha rows"\n')
    assert [h[2] for h in hits(load_names(), root)] == ["fixture-alpha"]


def test_control_2_the_same_key_inside_the_validator_library(tmp_path: Path) -> None:
    root = _tree(tmp_path, "backend/src/rules/validators/x.py", 'NAME = "fixture-alpha"\n')
    assert [h[2] for h in hits(load_names(), root)] == ["fixture-alpha"]


def test_control_3_a_vocabulary_term_as_an_identifier(tmp_path: Path) -> None:
    root = _tree(tmp_path, "k8s/x.yaml", "metadata:\n  name: ok\n")
    _tree(tmp_path, "backend/src/y.py", "def count_quorbishRows():\n    return 0\n")
    assert [h[2] for h in hits(load_names(), root)] == ["quorbish"]


def test_control_4_an_empty_name_source_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "names.json"
    empty.write_text(json.dumps({"behaviours": [], "names": [], "name_count": 0}))
    monkeypatch.setattr("tests.unit.test_behaviour_names_absent.NAMES_PATH", empty)
    with pytest.raises(AssertionError, match="empty"):
        load_names()
    from scripts.write_behaviour_names import NameSourceError

    with pytest.raises(NameSourceError):
        collect(tmp_path)


def test_a_vocabulary_term_equal_to_a_validator_component_is_refused(tmp_path: Path) -> None:
    from scripts.write_behaviour_names import NameSourceError

    pack = tmp_path / "behaviours" / "p" / "behaviour.json"
    pack.parent.mkdir(parents=True)
    pack.write_text(json.dumps({"behaviour_key": "p-key", "vocabulary": ["ssn"]}))
    with pytest.raises(NameSourceError, match="validator id"):
        collect(tmp_path)


def test_stale_debt_is_red(tmp_path: Path) -> None:
    root = _tree(tmp_path, "backend/src/z.py", "x = 1\n")
    assert stale_debt(
        [{"path": "backend/src/z.py", "token": "quorbish", "owner": "o", "reason": "r"}], root
    )


def test_debt_in_a_mirror_stripped_file_is_unchecked_not_stale_there(tmp_path: Path) -> None:
    """Review R1: on the mirror (root scripts/ stripped) such an entry is unchecked, never stale;
    an absent file the mirror does NOT strip stays stale."""
    root = _tree(tmp_path, "backend/src/z.py", "x = 1\n")
    unchecked: list[str] = []
    entries = [
        {"path": "scripts/gone.py", "token": "quorbish", "owner": "o", "reason": "r"},
        {"path": "backend/src/gone.py", "token": "quorbish", "owner": "o", "reason": "r"},
    ]
    assert [e["path"] for e in stale_debt(entries, root, unchecked)] == ["backend/src/gone.py"]
    assert unchecked == ["scripts/gone.py"]
