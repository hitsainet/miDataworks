"""``derive_labels@1``: the label, the deployed truth, the sub-question labels and the conflict
column, from declared rules (017 FR-017.25 – FR-017.29, FR-017.24; FTID §3.5; TD-017.4, TD-017.5).

A **labeler** (it adds columns; it drops only a decoy that a positive signal fired on). Nothing is
taken from a generator's output alone: the label is the declared truth table over what the PLAN
placed and declared (kind, framing, form, planned answers) and over boolean facts the build wrote
(``element_present``, ``element_recovered`` - the normaliser's verdict -, a rule set's ``fired``
outputs). Every column a table reads is reported, so the role's ``label_source_columns`` can list
them (FR-017.26; D-3 sets them aside).

- ``<label_column>``: the label table's answer. Rows the veto covers are labelled like any other
  (FR-017.25): the veto applies ONLY in ``deployed_truth``.
- ``deployed_truth`` (FR-017.28): ``label AND NOT <vetoed_column>`` when a veto column is named
  (016's ``<prefix>vetoed``); otherwise equal to the label, and the report says
  ``veto: "none declared"``.
- ``sq_<name>`` (FR-017.29): each declared sub-question's own table.
- ``label_conflict`` / ``label_conflict_reason`` (FR-017.27): the rules against the plan, NULL
  ("not measured") when the step is given no rule signal - neither a ``scan_column`` nor
  ``target_checks``; never ``false`` for a behaviour with nothing to measure. With a scan (016's
  all-candidates ``spans`` output, positions in the final text, and the pinned rule set):
  ``span_mismatch`` when the inserted element fires on its own (the scan's expression, evaluated on
  the element alone) but no candidate overlaps its planned span; ``extra_candidate`` when a
  candidate lies outside the planned span. With ``target_checks``, ``target_contradicted`` when a
  fact column disagrees with what the row's planned answer expects (instructed carriers).
- Decoys (FR-017.24): a row ``insert_element@1`` filled with a decoy (``decoy_id``) whose
  ``must_not_fire`` output (with ``output_prefix``) fired is an ACCIDENTAL MATCH: dropped
  ``decoy_accidental_match`` naming the column, counted, never labelled. A must_not_fire column
  the input lacks is refused ``decoy_check_missing`` and a null one ``fact_not_boolean``: the
  check never passes by absence (Review R3).

Refusals and drop events name columns, declared values and keys, never row text. No behaviour is
named here.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....rules.rule_set import RuleSetDocument, SpansValue
from ....services.behaviour.truth_table import TruthTables, parse_truth_tables, tables_problem
from ....services.behaviour.vocabulary import Vocabulary, VocabularyError, parse_vocabulary
from ...context import RunContext
from ...errors import StepFailed
from ...protocol import OperatorResult
from ..curation.common import manifest
from .common import (
    NEXT_RULE_SET,
    NEXT_VALIDATE,
    PIN_SCHEMA,
    deferred,
    json_param,
    refused,
    rule_set_of,
    vocabulary_of,
)

NAME = "derive_labels"
DEPLOYED = "deployed_truth"
CONFLICT = "label_conflict"
CONFLICT_REASON = "label_conflict_reason"
SPAN_MISMATCH = "span_mismatch"
EXTRA_CANDIDATE = "extra_candidate"
TARGET_CONTRADICTED = "target_contradicted"
NONE_DECLARED = "none declared"
NOT_MEASURED = "not measured"
COLUMN = "^[A-Za-z_][A-Za-z0-9_]{0,127}$"


def _target_checks(params: Mapping[str, Any], vocabulary: Vocabulary) -> list[dict[str, Any]]:
    raw = json_param(params, "target_checks", [])
    declared = {sq.name: sq for sq in vocabulary.sub_questions}
    if not isinstance(raw, list):
        raise VocabularyError(
            "vocabulary_invalid",
            "target_checks is a list of {sub_question, column, expect}.",
            {"field": "target_checks"},
        )
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"sub_question", "column", "expect"}:
            raise VocabularyError(
                "vocabulary_invalid",
                "Each target check is {sub_question, column, expect: {answer: true|false}}.",
                {"field": "target_checks"},
            )
        sq = declared.get(item["sub_question"])
        if sq is None:
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"target_checks names sub-question {item['sub_question']!r}, which the "
                "vocabulary does not declare.",
                {"field": "target_checks", "value": item["sub_question"]},
            )
        expect = item["expect"]
        if (
            not isinstance(expect, dict)
            or set(expect) - set(sq.values)
            or not all(isinstance(v, bool) for v in expect.values())
        ):
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"target_checks.expect for {sq.name!r} maps its declared answers "
                f"{list(sq.values)} to true or false.",
                {"field": "target_checks", "value": sq.name},
            )
    return list(raw)


def check_label_params(params: Mapping[str, Any]) -> list[str]:
    if params.get("scan_column") and not any(
        params.get(k) for k in ("rule_set_name", "rule_set_document")
    ):
        return [
            "/scan_column: params_invalid: the conflict scan needs the pinned rule set whose "
            "spans output it reads (rule_set_name, rule_set_version, rule_set_sha256)."
        ]
    if deferred(params.get("vocabulary")):
        return []
    try:
        vocabulary = parse_vocabulary(params.get("vocabulary"))
        _target_checks(params, vocabulary)
    except VocabularyError as exc:
        return [exc.problem("/vocabulary")]
    return tables_problem(params.get("truth_tables"), vocabulary)


def _scan_expression(document: RuleSetDocument, column: str, prefix: str) -> str:
    """The expression whose spans ``column`` (a prefixed ``spans`` output) holds."""
    for out in document.outputs:
        if prefix + out.column == column and isinstance(out.value, SpansValue):
            return out.value.spans
    raise StepFailed(
        "scan_column_not_spans",
        f"{column!r} is not a spans output of rule set {document.name} (prefix {prefix!r}); the "
        "conflict scan reads the rule set's all-candidates spans.",
        {"column": column, "rule_set": document.name, "next": NEXT_RULE_SET},
    )


def _spans(value: Any, column: str, key: str) -> list[tuple[int, int]]:
    if value is None:
        raise StepFailed(
            "scan_value_missing",
            f"Row {key[:12]} has no {column!r}: the scan did not run on it.",
            {"column": column, "row_key": key, "next": NEXT_VALIDATE},
        )
    return [(int(s), int(e)) for s, e in json.loads(str(value))]


def _bool(row: Mapping[str, Any], column: str, key: str) -> bool:
    value = row.get(column)
    if not isinstance(value, bool):
        raise StepFailed(
            "fact_not_boolean",
            f"Row {key[:12]}: {column!r} is {type(value).__name__}, not true or false.",
            {"column": column, "row_key": key, "next": NEXT_VALIDATE},
        )
    return value


class DeriveLabels:
    manifest = manifest(
        NAME,
        "labeler",
        "Writes the label, the deployed truth (the label after the declared veto), one sq_<name> "
        "per declared sub-question and the conflict column, from declared truth tables over the "
        "plan's columns and the rule signals' facts - never from a generator's output alone. "
        "Drops a decoy a positive signal fired on.",
        params={
            "label_column": {
                "type": "string",
                "pattern": COLUMN,
                "title": "Label column",
                "x-hint": "The behaviour's label column name (behaviour data).",
            },
            "vocabulary": {
                "type": "string",
                "minLength": 2,
                "title": "Vocabulary",
                "x-widget": "textarea",
                "x-hint": '"@input" reads the input version\'s recorded vocabulary.',
            },
            "truth_tables": {
                "type": "string",
                "minLength": 2,
                "title": "Truth tables (JSON)",
                "x-widget": "textarea",
                "x-hint": '{"label": {inputs, rows: [{when, value}]}, "sub_questions": '
                '{"<name>": {inputs, rows}}}; "*" matches any value, the first matching row '
                "answers, every combination must be answered.",
            },
            "vetoed_column": {
                "type": "string",
                "pattern": COLUMN,
                "title": "Veto column",
                "x-hint": "rule_set@1's <prefix>vetoed; absent, deployed_truth equals the label.",
            },
            "scan_column": {
                "type": "string",
                "pattern": COLUMN,
                "title": "All-candidates scan column",
                "x-hint": "The rule set's spans output (prefixed) over its positive signal.",
            },
            "target_checks": {
                "type": "string",
                "default": "[]",
                "title": "Target checks (JSON)",
                "x-widget": "textarea",
                "x-hint": "[{sub_question, column, expect: {answer: true|false}}]: a fact "
                "column that disagrees with the planned answer is target_contradicted.",
            },
            "output_prefix": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_]{0,40}$",
                "default": "rs_",
                "title": "Rule set output prefix",
                "x-hint": "The rule_set@1 step's output_prefix (decoys' must_not_fire columns, "
                "the scan's output).",
            },
            **PIN_SCHEMA,
        },
        required=("label_column", "vocabulary", "truth_tables"),
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_label_params(params)

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads = [str(params[c]) for c in ("vetoed_column", "scan_column") if params.get(c)]
        writes = [str(params.get("label_column")), DEPLOYED, CONFLICT, CONFLICT_REASON]
        vocab = params.get("vocabulary")
        if not deferred(vocab):
            try:
                vocabulary = parse_vocabulary(vocab)
                tables = parse_truth_tables(params.get("truth_tables"), vocabulary)
                reads += [c for c in tables.inputs() if c not in reads]
                writes += [f"sq_{t.name}" for t in tables.sub_questions]
            except VocabularyError:
                pass  # reported by check_params
        return reads, writes, []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            vocabulary = vocabulary_of(params)
            tables: TruthTables = parse_truth_tables(params.get("truth_tables"), vocabulary)
            checks = _target_checks(params, vocabulary)
        except VocabularyError as exc:
            raise refused(exc) from None
        document = rule_set_of(params, self.manifest.ref_text)
        prefix = str(params.get("output_prefix") if params.get("output_prefix") is not None
                     else "rs_")  # fmt: skip
        label_column = str(params["label_column"])
        vetoed_column = params.get("vetoed_column")
        scan_column = params.get("scan_column")
        if scan_column and document is None:
            raise StepFailed(
                "scan_needs_rule_set",
                "The conflict scan needs the pinned rule set whose spans output it reads.",
                {"column": scan_column, "next": NEXT_RULE_SET},
            )
        # imported here: ``rules.validators`` loads the identifier schemes, which load this
        # package while the validator registry initialises (as ``rule_set@1`` does)
        from ....rules.compose import CompiledRuleSet

        compiled = CompiledRuleSet(document) if document is not None else None
        expression = (
            _scan_expression(document, str(scan_column), prefix)
            if scan_column and document is not None
            else None
        )
        sq_names = [t.name for t in tables.sub_questions]
        written = [label_column, DEPLOYED, CONFLICT, CONFLICT_REASON] + [
            f"sq_{n}" for n in sq_names
        ]
        clash = [c for c in written if c in batch.schema.names]
        if clash:
            raise StepFailed(
                "output_column_exists",
                f"derive_labels@1 writes {clash}, which the input already has.",
                {"columns": clash},
            )
        missing = [
            c
            for c in [*tables.inputs(), *(c for c in (vetoed_column, scan_column) if c)]
            + [c["column"] for c in checks]
            + [f"planned_{c['sub_question']}" for c in checks]
            if c not in batch.schema.names
        ]
        if missing:
            raise StepFailed(
                "column_not_found",
                f"derive_labels@1 reads {sorted(set(missing))}, which the input does not have.",
                {"columns": sorted(set(missing))},
            )
        measured = bool(scan_column) or bool(checks)
        out: dict[str, list[Any]] = {c: [] for c in written}
        keep: list[int] = []
        events = []
        reasons: Counter[str] = Counter()
        decoys_excluded: Counter[str] = Counter()
        vetoed_rows = 0
        decoy_by_id = {d.id: d for d in document.decoys} if document is not None else {}
        for index, row in enumerate(batch.to_pylist()):
            key = str(row["_dw_row_key"])
            decoy_id = row.get("decoy_id")
            if decoy_id is not None:
                decoy = decoy_by_id.get(str(decoy_id))
                if decoy is None:
                    raise StepFailed(
                        "decoy_not_declared",
                        f"Row {key[:12]} holds decoy {decoy_id!r}, which the pinned rule set "
                        "does not declare (pin the rule set insert_element@1 used).",
                        {"decoy": decoy_id, "row_key": key, "next": NEXT_RULE_SET},
                    )
                # Review R3: a decoy's check never passes by absence. A must_not_fire column the
                # input lacks (no rule_set@1 step, another output_prefix) or a null one (the scan
                # did not reach the row) is refused, never read as "did not fire".
                checked = [prefix + c for c in decoy.must_not_fire]
                absent = [c for c in checked if c not in batch.schema.names]
                if absent:
                    raise StepFailed(
                        "decoy_check_missing",
                        f"Row {key[:12]} holds decoy {decoy_id!r}, whose must_not_fire output(s) "
                        f"{absent} the input does not have: run the pinned rule set (rule_set@1) "
                        "before this step with the same output_prefix.",
                        {"decoy": decoy_id, "columns": absent, "next": NEXT_VALIDATE},
                    )
                fired = [c for c in checked if _bool(row, c, key)]
                if fired:
                    decoys_excluded[str(decoy_id)] += 1
                    events.append(
                        ctx.drop(
                            (key, int(row["_dw_occurrence"])),
                            "decoy_accidental_match",
                            f"decoy {decoy_id!r} fired {fired[0]!r}: an accidental match, "
                            "excluded",
                            fired[0],
                            text=f"{fired[0]}=True",
                        )
                    )
                    continue
            try:
                label = bool(tables.label.evaluate(row))
                answers = {t.name: t.evaluate(row) for t in tables.sub_questions}
            except VocabularyError as exc:
                raise StepFailed(exc.code, exc.message, {**exc.details, "row_key": key}) from None
            if vetoed_column:
                vetoed = _bool(row, str(vetoed_column), key)
                vetoed_rows += int(vetoed)
                deployed = label and not vetoed
            else:
                deployed = label
            reason: str | None = None
            if measured:
                if scan_column and expression is not None and compiled is not None:
                    candidates = _spans(row.get(str(scan_column)), str(scan_column), key)
                    start, end = row.get("ins_start"), row.get("ins_end")
                    planned = (
                        (int(start), int(end))
                        if start is not None and end is not None and end > start
                        else None
                    )
                    if planned is not None:
                        element = str(row.get("element_inserted") or "")
                        alone = compiled.evaluate(element, element).expressions[expression]
                        if alone["fired"] and not any(
                            s < planned[1] and planned[0] < e for s, e in candidates
                        ):
                            reason = SPAN_MISMATCH
                    if reason is None and any(
                        planned is None or not (s < planned[1] and planned[0] < e)
                        for s, e in candidates
                    ):
                        reason = EXTRA_CANDIDATE
                if reason is None:
                    for check in checks:
                        planned_answer = row.get(f"planned_{check['sub_question']}")
                        if planned_answer not in check["expect"]:
                            continue
                        if _bool(row, check["column"], key) != check["expect"][planned_answer]:
                            reason = TARGET_CONTRADICTED
                            break
                reasons[reason or "none"] += 1
            keep.append(index)
            out[label_column].append(label)
            out[DEPLOYED].append(deployed)
            out[CONFLICT].append((reason is not None) if measured else None)
            out[CONFLICT_REASON].append(reason)
            for name in sq_names:
                out[f"sq_{name}"].append(answers[name])
        output = batch.take(pa.array(keep, pa.int64())) if len(keep) != batch.num_rows else batch
        types = {label_column: pa.bool_(), DEPLOYED: pa.bool_(), CONFLICT: pa.bool_()}
        for column in written:
            output = output.append_column(
                column, pa.array(out[column], types.get(column, pa.string()))
            )
        return OperatorResult(
            output=output,
            events=events,
            output_roles=dict.fromkeys(written, "metadata"),
            report={
                "labels": {
                    "label_column": label_column,
                    "label_source_columns": tables.inputs(),
                    "sub_questions": [f"sq_{n}" for n in sq_names],
                    "veto": (
                        {"column": vetoed_column, "rows_vetoed": vetoed_rows}
                        if vetoed_column
                        else NONE_DECLARED
                    ),
                    "conflict": (dict(sorted(reasons.items())) if measured else NOT_MEASURED),
                    "decoys_excluded": dict(sorted(decoys_excluded.items())),
                }
            },
        )
