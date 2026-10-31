"""Fixture code rules (feature 018; FTDD §11.6, FR-018.45, FR-018.47).

Two behaviours with random keys (``fixture-gamma``, ``fixture-delta``) and rules that share no
vocabulary, so a test cannot pass by fields agreeing by construction. Each fixture is a document
(the hashed body), its cases (not hashed), and a PYTHON TWIN of its function for
``fake_runner.FakeRunner`` - the backend tests never execute a code rule's source (FR-018.18);
the real runner runs the same documents in ``runner/tests``.

- ``digit_sum`` (fixture 2 of the contract's conformance set): counts whitespace tokens whose
  decimal digit sum is in ``params.sums`` - nothing declarative can say it.
- ``loud_words``: counts tokens written entirely in capitals of length >= ``params.min``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from src.runner_contract.hashing import code_sha256

DIGIT_SUM_SOURCE = (
    "def digit_sum(text, params):\n"
    "    wanted = set(params['sums'])\n"
    "    n = 0\n"
    "    for token in text.split():\n"
    "        if token.isdigit() and sum(int(c) for c in token) in wanted:\n"
    "            n += 1\n"
    "    return n\n"
)
LOUD_SOURCE = (
    "def loud_words(text, params):\n"
    "    return sum(1 for t in text.split() if t.isalpha() and t.isupper() "
    "and len(t) >= params['min'])\n"
)


def _digit_sum(text: str, params: dict[str, Any]) -> int:
    wanted = set(params["sums"])
    return sum(1 for t in text.split() if t.isdigit() and sum(int(c) for c in t) in wanted)


def _loud(text: str, params: dict[str, Any]) -> int:
    return sum(1 for t in text.split() if t.isalpha() and t.isupper() and len(t) >= params["min"])


def _doc(source: str, function: str, schema: dict[str, Any], statement: str) -> dict[str, Any]:
    return {
        "runner_contract": "midataworks.runner/v1",
        "source": source,
        "functions": [{"name": function, "output": {"type": "count"}}],
        "params_schema": schema,
        "derived_from": {"sources": [], "statement": statement},
    }


def _case(case_id: str, function: str, params: dict[str, Any], text: str, n: int) -> dict[str, Any]:
    return {
        "id": case_id,
        "function": function,
        "params": params,
        "text": text,
        "expect": {"count": n},
        "source": {"kind": "constructed"},
    }


FIXTURES: dict[str, dict[str, Any]] = {
    "fixture-gamma": {
        "name": "gamma-digit-sum",
        "document": _doc(
            DIGIT_SUM_SOURCE,
            "digit_sum",
            {
                "type": "object",
                "properties": {"sums": {"type": "array", "items": {"type": "integer"}}},
                "required": ["sums"],
            },
            "Constructed fixture: tokens by digit sum.",
        ),
        "cases": [
            _case("fires", "digit_sum", {"sums": [10, 20]}, "28 73 46 11", 3),
            _case("quiet", "digit_sum", {"sums": [10]}, "11 12 13", 0),
        ],
        "twin": {"digit_sum": _digit_sum},
    },
    "fixture-delta": {
        "name": "delta-loud-words",
        "document": _doc(
            LOUD_SOURCE,
            "loud_words",
            {
                "type": "object",
                "properties": {"min": {"type": "integer", "minimum": 1}},
                "required": ["min"],
            },
            "Constructed fixture: words in capitals.",
        ),
        "cases": [
            _case("fires", "loud_words", {"min": 3}, "THE cat SAT", 2),
            _case("quiet", "loud_words", {"min": 3}, "no capitals here", 0),
        ],
        "twin": {"loud_words": _loud},
    },
}


def fixture(behaviour: str) -> dict[str, Any]:
    """A fresh copy: ``{name, document, cases, twin}``."""
    data = FIXTURES[behaviour]
    return {
        "name": data["name"],
        "document": copy.deepcopy(data["document"]),
        "cases": copy.deepcopy(data["cases"]),
        "twin": dict(data["twin"]),
    }


def save_body(behaviour: str, **over: Any) -> dict[str, Any]:
    f = fixture(behaviour)
    body = {
        "name": f["name"],
        "behaviour_key": behaviour,
        "document": f["document"],
        "cases": f["cases"],
    }
    body.update(over)
    return body


def twins_by_hash() -> dict[str, dict[str, Callable[[str, dict[str, Any]], int]]]:
    """Each fixture document's hash -> its functions' Python twins (what the fake runner runs)."""
    return {code_sha256(f["document"]): f["twin"] for f in FIXTURES.values()}


# --- feature 018 slice 4: a rule set naming a fixture code rule (FTASKS 13.x) ---------------------

#: Per behaviour: the code rule's function and params, a pattern word, and three case texts
#: (both fire; nothing fires; only the word fires), with the code rule's count on each.
RULE_SET_SHAPES: dict[str, dict[str, Any]] = {
    "fixture-gamma": {
        "set_name": "gamma-sum-set",
        "function": "digit_sum",
        "params": {"sums": [10, 20]},
        "word": "tally",
        "texts": ("tally 28 73", "11 12 13", "tally 11"),
        "counts": (2, 0, 0),
    },
    "fixture-delta": {
        "set_name": "delta-loud-set",
        "function": "loud_words",
        "params": {"min": 3},
        "word": "shouts",
        "texts": ("shouts THE CAT", "no capitals here", "shouts quietly"),
        "counts": (2, 0, 0),
    },
}


def _rs_case(case_id: str, text: str, count: int, word: bool) -> dict[str, Any]:
    hit = count >= 1
    return {
        "id": case_id,
        "input": {"text": text},
        "expect": {
            "outputs": {
                "hit": hit,
                "n": count,
                "both": hit and word,
                "either": hit or word,
                "quiet": not hit,
                "worded": word,
            },
            "vetoed": False,
        },
        "source": {"kind": "constructed"},
        "note": "constructed fixture text",
    }


def rule_set_document(behaviour: str, code_sha256: str, **signal_over: Any) -> dict[str, Any]:
    """A rule set with one pattern and one code-rule signal composed by ``all``, ``any`` and
    ``not`` (FR-018.37), whose cases hold only when the runner's counts are used."""
    shape = RULE_SET_SHAPES[behaviour]
    signal = {
        "id": "coded",
        "kind": "code_rule",
        "code_sha256": code_sha256,
        "runner_contract": "midataworks.runner/v1",
        "function": shape["function"],
        "params": copy.deepcopy(shape["params"]),
        "outputs": [{"name": "n", "type": "int"}],
        **signal_over,
    }
    word = rf"\b{shape['word']}\b"
    texts, counts = shape["texts"], shape["counts"]
    return {
        "format": "midataworks.rule-set/v1",
        "name": shape["set_name"],
        "behaviour_key": behaviour,
        "description": "A constructed fixture rule set naming a code rule.",
        "signals": [
            {
                "id": "word",
                "kind": "pattern",
                "pattern": word,
                "ignore_case": False,
                "fold_fullwidth_ascii": False,
                "reads": "text",
            },
            signal,
        ],
        "expressions": {
            "hit": {"signal": "coded"},
            "both": {"all": [{"signal": "word"}, {"signal": "coded"}]},
            "either": {"any": [{"signal": "word"}, {"signal": "coded"}]},
            "quiet": {"not": {"signal": "coded"}},
            "worded": {"signal": "word"},
        },
        "outputs": [
            {"column": "hit", "type": "bool", "value": {"fired": "hit"}},
            {"column": "n", "type": "int", "value": {"count": "hit"}},
            {"column": "both", "type": "bool", "value": {"fired": "both"}},
            {"column": "either", "type": "bool", "value": {"fired": "either"}},
            {"column": "quiet", "type": "bool", "value": {"fired": "quiet"}},
            {"column": "worded", "type": "bool", "value": {"fired": "worded"}},
        ],
        "cases": [
            _rs_case("both", texts[0], counts[0], True),
            _rs_case("none", texts[1], counts[1], False),
            _rs_case("word-only", texts[2], counts[2], True),
        ],
    }


def expected_columns(behaviour: str, text: str) -> dict[str, Any]:
    """The columns ``rule_set@1`` writes for ``text`` (computed from the Python twin, never from
    the rule set's own cases: a test cannot pass by the fields agreeing by construction)."""
    import re

    shape = RULE_SET_SHAPES[behaviour]
    count = fixture(behaviour)["twin"][shape["function"]](text, shape["params"])
    word = re.search(rf"\b{shape['word']}\b", text) is not None
    return {
        "hit": count >= 1,
        "n": count,
        "both": count >= 1 and word,
        "either": count >= 1 or word,
        "quiet": count < 1,
        "worded": word,
    }


# --- fixture 1: the Luhn re-implementation (FTDD §11.6; FR-018.45) ------------------------------


def luhn_document() -> dict[str, Any]:
    """Fixture 1 exactly as the contract's conformance vectors publish it (``fixture1_luhn``)."""
    from src.runner_contract.cases import load_cases

    return copy.deepcopy(load_cases()["documents"]["fixture1_luhn"])


def luhn_twin(text: str, params: dict[str, Any]) -> int:
    """The published source's algorithm, in the host, for the fake runner only (the backend never
    runs the source, FR-018.18): digit runs of 12+ digits (spaces and dashes inside) whose Luhn
    sum is 0 mod 10."""
    import re

    del params
    n = 0
    for run in re.findall(r"[0-9][0-9 -]{10,30}[0-9]", text):
        digits = [int(c) for c in run if c.isdigit()]
        if len(digits) < 12:
            continue
        total, parity = 0, len(digits) % 2
        for i, d in enumerate(digits):
            if i % 2 == parity:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        n += total % 10 == 0
    return n
