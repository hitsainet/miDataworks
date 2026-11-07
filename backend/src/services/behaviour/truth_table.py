"""Truth tables: a behaviour's declared label and sub-question rules (017 FR-017.25, FR-017.29;
FTID §3.5; TD-017.4, TD-017.5).

A truth table maps the values of its declared INPUTS to one answer. It is behaviour data, passed
to ``derive_labels@1`` as JSON text (FTID I14: the operator schema subset has no nested objects)::

    {"label": {"inputs": ["kind", "framing"],
               "rows": [{"when": {"kind": "valid", "framing": "*"}, "value": true}, ...]},
     "sub_questions": {"<name>": {"inputs": [...], "rows": [...]}}}

An input is a vocabulary dimension (``genre``, ``placement``, ``framing``, ``kind``, ``form``,
``length_band``), a planned sub-question answer (``planned_<name>``), or any other column, which
is read as a boolean fact (a rule set's ``fired`` output, ``element_present``,
``element_recovered``). ``"*"`` in ``when`` matches any value; rows are read in order and the
FIRST row that matches answers. A label answers ``true``/``false``; a sub-question answers one of
its declared values.

**Total, or refused** (TD-017.4): every combination of the inputs' declared values must be answered
by some row, checked when the table is parsed (``truth_table_not_total`` naming the table and the
first combinations no row answers), so a build never meets a row its rule cannot label. A row an
earlier row shadows with a different answer is refused ``truth_table_row_shadowed`` (Review R3: the
build would silently ignore it). Every
declared sub-question needs its table, and a table for an undeclared one is refused. Nothing here
names a behaviour: inputs, values and answers are the behaviour's data.
"""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .vocabulary import Vocabulary, VocabularyError

DIMENSIONS: tuple[str, ...] = ("genre", "placement", "framing", "kind", "form", "length_band")
WILDCARD = "*"
LABEL = "label"
#: The most input combinations one table may enumerate (each is checked for totality).
MAX_COMBINATIONS = 100_000
MAX_ROWS = 10_000
COLUMN_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
BOOLEAN_DOMAIN: tuple[bool, ...] = (True, False)


def _refuse(code: str, message: str, details: Mapping[str, Any] | None = None) -> VocabularyError:
    return VocabularyError(code, message, {"field": "truth_tables", **(details or {})})


@dataclass(frozen=True, eq=False)
class TruthTable:
    """One parsed table: ``answers`` holds every combination of ``inputs`` (total by
    construction), so evaluation is a lookup."""

    name: str
    inputs: tuple[str, ...]
    answers: Mapping[tuple[Any, ...], Any]
    #: ``True`` for a label (booleans), else the sub-question's declared answers.
    boolean: bool
    #: ``(input, value)`` for every declared value of every input (the refusal's wording).
    declared: frozenset[tuple[str, Any]]

    def evaluate(self, row: Mapping[str, Any]) -> Any:
        """The answer for one row; refuses a value outside the declared domain (a null fact, an
        undeclared plan value), naming the input and the value, never the row's text."""
        key = tuple(row.get(i) for i in self.inputs)
        try:
            return self.answers[key]
        except KeyError:
            bad = next(
                (i, v) for i, v in zip(self.inputs, key, strict=True) if (i, v) not in self.declared
            )
            raise _refuse(
                "truth_table_input_undeclared",
                f"Truth table {self.name!r} has no answer for {bad[0]} = {bad[1]!r}: the value is "
                "not declared (a fact column must be true or false; a plan column must hold a "
                "declared value).",
                {"table": self.name, "input": bad[0], "value": bad[1]},
            ) from None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "inputs": list(self.inputs), "boolean": self.boolean}


@dataclass(frozen=True)
class TruthTables:
    label: TruthTable
    sub_questions: tuple[TruthTable, ...]

    def inputs(self) -> list[str]:
        """Every column any table reads, in first-use order (``label_source_columns``)."""
        seen: dict[str, None] = {}
        for table in (self.label, *self.sub_questions):
            seen.update(dict.fromkeys(table.inputs))
        return list(seen)

    def fact_inputs(self) -> list[str]:
        return [c for c in self.inputs() if c not in DIMENSIONS and not c.startswith("planned_")]


def _domain(name: str, vocabulary: Vocabulary) -> tuple[Any, ...]:
    if name in DIMENSIONS:
        return vocabulary.values(name)
    if name.startswith("planned_"):
        sq = name[len("planned_") :]
        for declared in vocabulary.sub_questions:
            if declared.name == sq:
                return declared.values
        raise _refuse(
            "vocabulary_value_undeclared",
            f"A truth table reads {name!r}, but the vocabulary declares no sub-question {sq!r}.",
            {"value": name},
        )
    if not COLUMN_PATTERN.fullmatch(name):
        raise _refuse(
            "truth_table_invalid",
            f"Truth table input {name!r} is not a column name.",
            {"value": name},
        )
    return BOOLEAN_DOMAIN


def _matches(pattern: tuple[Any, ...], combination: tuple[Any, ...]) -> bool:
    return all(p == WILDCARD or p == v for p, v in zip(pattern, combination, strict=True))


def _refuse_shadowed(
    name: str,
    inputs: Sequence[str],
    parsed: Sequence[tuple[tuple[Any, ...], Any]],
    answers: Mapping[tuple[Any, ...], Any],
    answered_by: Mapping[tuple[Any, ...], int],
) -> None:
    """Review R3: first-match means an earlier (wildcard) row can SHADOW a later, more specific
    one. A row that answers no combination while an earlier row gives one of its combinations a
    DIFFERENT answer is a rule the author wrote and the build would silently ignore (the label
    would contradict it): refused ``truth_table_row_shadowed`` naming both rows and the
    combination. A dead row that agrees with what answers it is harmless and kept."""
    used = set(answered_by.values())
    for position, (pattern, answer) in enumerate(parsed):
        if position in used:
            continue
        for combination, given in answers.items():
            if given != answer and _matches(pattern, combination):
                by = answered_by[combination]
                raise _refuse(
                    "truth_table_row_shadowed",
                    f"Truth table {name!r} row {position} (answer {answer!r}) is never read: row "
                    f"{by} matches first and answers {given!r} for "
                    f"{dict(zip(inputs, combination, strict=True))}. Move the more specific row "
                    "above the one that shadows it.",
                    {
                        "table": name,
                        "row": position,
                        "shadowed_by": by,
                        "combination": dict(zip(inputs, combination, strict=True)),
                    },
                )


def _parse_one(
    name: str, raw: Any, vocabulary: Vocabulary, answers_domain: tuple[Any, ...] | None
) -> TruthTable:
    if not isinstance(raw, dict) or set(raw) != {"inputs", "rows"}:
        raise _refuse(
            "truth_table_invalid",
            f"Truth table {name!r} is {{inputs, rows}}.",
            {"table": name},
        )
    inputs = raw["inputs"]
    if (
        not isinstance(inputs, list)
        or not inputs
        or not all(isinstance(i, str) for i in inputs)
        or len(set(inputs)) != len(inputs)
    ):
        raise _refuse(
            "truth_table_invalid",
            f"Truth table {name!r}: inputs is a non-empty list of distinct column names.",
            {"table": name},
        )
    domains = [_domain(i, vocabulary) for i in inputs]
    combinations = 1
    for d in domains:
        combinations *= len(d)
    if combinations > MAX_COMBINATIONS:
        raise _refuse(
            "truth_table_invalid",
            f"Truth table {name!r} spans {combinations:,} input combinations; at most "
            f"{MAX_COMBINATIONS:,}. Read fewer inputs.",
            {"table": name, "limit": MAX_COMBINATIONS, "actual": combinations},
        )
    rows = raw["rows"]
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise _refuse(
            "truth_table_invalid",
            f"Truth table {name!r}: rows is a list of 1 to {MAX_ROWS:,} {{when, value}}.",
            {"table": name},
        )
    parsed: list[tuple[tuple[Any, ...], Any]] = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"when", "value"}:
            raise _refuse(
                "truth_table_invalid",
                f"Truth table {name!r} row {position} is {{when, value}}.",
                {"table": name, "row": position},
            )
        when = row["when"]
        if not isinstance(when, dict) or set(when) - set(inputs):
            raise _refuse(
                "truth_table_invalid",
                f"Truth table {name!r} row {position}: when names only its inputs {inputs}.",
                {"table": name, "row": position},
            )
        pattern = []
        for column, domain in zip(inputs, domains, strict=True):
            value = when.get(column, WILDCARD)
            if value != WILDCARD and (
                value not in domain or isinstance(value, bool) != (domain is BOOLEAN_DOMAIN)
            ):
                raise _refuse(
                    "vocabulary_value_undeclared",
                    f"Truth table {name!r} row {position}: {column} = {value!r} is not declared "
                    f"(declared: {list(domain)}).",
                    {"table": name, "row": position, "input": column, "value": value},
                )
            pattern.append(value)
        answer = row["value"]
        if answers_domain is None:
            if not isinstance(answer, bool):
                raise _refuse(
                    "truth_table_invalid",
                    f"Truth table {name!r} row {position}: a label answers true or false.",
                    {"table": name, "row": position},
                )
        elif answer not in answers_domain or isinstance(answer, bool):
            raise _refuse(
                "vocabulary_value_undeclared",
                f"Truth table {name!r} row {position} answers {answer!r}; the sub-question's "
                f"answers are {list(answers_domain)}.",
                {"table": name, "row": position, "value": answer},
            )
        parsed.append((tuple(pattern), answer))
    answers: dict[tuple[Any, ...], Any] = {}
    answered_by: dict[tuple[Any, ...], int] = {}
    missing: list[dict[str, Any]] = []
    for combination in itertools.product(*domains):
        for position, (pattern_, answer) in enumerate(parsed):
            if _matches(pattern_, combination):
                answers[combination] = answer
                answered_by[combination] = position
                break
        else:
            missing.append(dict(zip(inputs, combination, strict=True)))
    if missing:
        raise _refuse(
            "truth_table_not_total",
            f"Truth table {name!r} answers no row for {len(missing):,} combination(s) of its "
            f"declared inputs; first: {missing[0]}. Add rows (or a wildcard row) for them.",
            {"table": name, "missing": missing[:5], "missing_count": len(missing)},
        )
    _refuse_shadowed(name, inputs, parsed, answers, answered_by)
    declared = frozenset((i, v) for i, d in zip(inputs, domains, strict=True) for v in d)
    return TruthTable(name, tuple(inputs), answers, answers_domain is None, declared)


def parse_truth_tables(text: Any, vocabulary: Vocabulary) -> TruthTables:
    """The label table and one table per declared sub-question, each total over the vocabulary."""
    if isinstance(text, (dict, list)):
        raw = text
    else:
        try:
            raw = json.loads(str(text))
        except ValueError as exc:
            raise _refuse("truth_table_invalid", f"truth_tables is not JSON: {exc}.") from None
    if not isinstance(raw, dict) or set(raw) - {LABEL, "sub_questions"} or LABEL not in raw:
        raise _refuse(
            "truth_table_invalid",
            'truth_tables is {"label": {...}, "sub_questions": {"<name>": {...}}}.',
        )
    label = _parse_one(LABEL, raw[LABEL], vocabulary, None)
    given = raw.get("sub_questions") or {}
    if not isinstance(given, dict):
        raise _refuse("truth_table_invalid", "truth_tables.sub_questions is an object.")
    declared = {sq.name: sq for sq in vocabulary.sub_questions}
    undeclared = sorted(set(given) - set(declared))
    if undeclared:
        raise _refuse(
            "vocabulary_value_undeclared",
            f"truth_tables names sub-question(s) {undeclared} the vocabulary does not declare.",
            {"values": undeclared},
        )
    missing = [n for n in declared if n not in given]
    if missing:
        raise _refuse(
            "truth_table_not_total",
            f"Every declared sub-question needs its truth table; missing: {missing}.",
            {"missing_tables": missing},
        )
    return TruthTables(
        label,
        tuple(_parse_one(n, given[n], vocabulary, declared[n].values) for n in declared),
    )


def tables_problem(text: Any, vocabulary: Vocabulary) -> list[str]:
    """``check_params``' form (validation): ``[]`` or one problem naming the code."""
    try:
        parse_truth_tables(text, vocabulary)
    except VocabularyError as exc:
        return [exc.problem("/truth_tables")]
    return []


def column_names(sub_questions: Sequence[str]) -> list[str]:
    return [f"sq_{n}" for n in sub_questions]
