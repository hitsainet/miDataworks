"""``insert_element@1``: put a behaviour's element into its carrier, recording where (017 FR-017.22,
FR-017.23, FR-017.24, FR-017.12; FTID §3.4; TD-017.21).

A **mapper** over carrier rows (row scope). Every offset is in Unicode code points (Python ``str``
indices), never bytes, so ``text[ins_start:ins_end] == element_inserted`` holds on multi-byte text.

- ``mode = slot``: the row's ``slot`` column holds the carrier run's slot token; the text must
  hold it exactly once (``slot_missing`` / ``slot_repeated`` drop the row, counted). A row with no
  slot (placement ``none`` or ``whole``) passes unchanged with ``element_present = false``.
- ``mode = real_text``: the element goes at the row's ``placement`` (its column, else a seeded draw
  among the step's ``placements``, default start, middle and end); ``middle`` is the nearest line, sentence or whitespace
  boundary (``boundaries.py``). The row is CHANGED (``origin`` stays ``source``, FTDD §4.3) and
  gains the FR-017.12 columns it lacks: ``behaviour_key`` and ``definition_version`` (the
  definition), ``generator = real_text:<source id>``, ``carrier_id`` (its key before insertion),
  ``genre``/``kind``/``framing``/``form`` from ``real_text_values``, and its length band.

**The element.** A row whose ``kind`` is a declared decoy (vocabulary ``decoys``) takes a decoy
from the pinned rule set's generator of that id, seeded per row (FR-017.24); any other row takes
``element_column``. A null element inserts nothing (``element_present = false``).

**Renderings** (FR-017.23): a row whose ``form`` is a rendering the pinned rule set declares is
inserted RENDERED (016's ``rules/renderings.render``), and ``element_recovered`` records the
normaliser's verdict, ``normalise(rendered) == element``; ``element_rendering`` and
``element_normaliser`` record which (the label's rule provenance). A declared form that is no
rendering is inserted as given, and the report lists it. Both facts are booleans on every row
(a truth table reads them): with nothing inserted, ``element_present`` and ``element_recovered``
are false.

An optional framing phrase from the vocabulary's ``phrase_list`` (seeded share) goes just BEFORE
the element, outside the span; the span covers the element only. Rows with no text (a carrier
run's plan rows) are dropped ``no_carrier_text``. Drop events and refusals carry keys, columns and
declared values, never row text. No behaviour is named here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....rules import renderings as rendering_rules
from ....rules.rule_set import Decoy, GenerateList, Rendering, RuleSetDocument
from ....schemas.behaviour_datasets import RecordedDefinitionIn
from ....services.behaviour.vocabulary import Vocabulary, VocabularyError, parse_vocabulary
from ....services.row_keys import compute_row_key
from ...context import RunContext
from ...errors import StepFailed
from ...protocol import OperatorResult
from ..curation.common import manifest
from . import boundaries
from .common import (
    NEXT_RULE_SET,
    NEXT_VALIDATE,
    NEXT_VERSION,
    PIN_SCHEMA,
    deferred,
    json_param,
    refused,
    row_random,
    rule_set_of,
    vocabulary_of,
)

NAME = "insert_element"
SLOT_MODE = "slot"
REAL_TEXT_MODE = "real_text"
NOT_VERIFIED = "not verified"
#: Columns every row gets (FR-017.22; FTDD §4.3).
SPAN_COLUMNS: tuple[tuple[str, pa.DataType], ...] = (
    ("ins_start", pa.int64()),
    ("ins_end", pa.int64()),
    ("element_inserted", pa.string()),
    ("boundary_kind", pa.string()),
    ("element_present", pa.bool_()),
    ("element_recovered", pa.bool_()),
    ("element_rendering", pa.string()),
    ("element_normaliser", pa.string()),
    ("decoy_id", pa.string()),
    ("framing_phrase", pa.string()),
)
#: FR-017.12 columns a real-text row gains when it lacks them.
PROVENANCE_COLUMNS: tuple[str, ...] = (
    "behaviour_key",
    "definition_version",
    "generator",
    "carrier_id",
    "genre",
    "kind",
    "framing",
    "form",
    "placement",
    "length_band",
)
REAL_TEXT_VALUE_FIELDS = ("genre", "kind", "framing", "form")
SLOTTED = ("start", "middle", "end")


def _definition(params: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = params.get("definition")
    if raw is None or deferred(raw):
        return None
    parsed = RecordedDefinitionIn.model_validate(json_param(params, "definition", None))
    return parsed.model_dump()


def _real_text_values(params: Mapping[str, Any], vocabulary: Vocabulary) -> dict[str, str]:
    values = json_param(params, "real_text_values", {})
    if not isinstance(values, dict) or set(values) - set(REAL_TEXT_VALUE_FIELDS):
        raise VocabularyError(
            "vocabulary_invalid",
            f"real_text_values is an object of {list(REAL_TEXT_VALUE_FIELDS)} -> value.",
            {"field": "real_text_values"},
        )
    for field, value in values.items():
        if value not in vocabulary.values(field):
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"real_text_values.{field} = {value!r} is not declared "
                f"(declared: {list(vocabulary.values(field))}).",
                {"field": f"real_text_values.{field}", "value": value},
            )
    return {str(k): str(v) for k, v in values.items()}


def check_insert_params(params: Mapping[str, Any]) -> list[str]:
    """Cross-parameter checks at validation (an ``"@input"`` vocabulary waits for the build)."""
    mode = params.get("mode", SLOT_MODE)
    if mode == REAL_TEXT_MODE and params.get("definition") is None:
        return [
            "/definition: params_invalid: real_text mode records the behaviour key and "
            'definition version on each row: pass "@input" or the recorded definition.'
        ]
    try:
        _definition(params)
        if not deferred(params.get("vocabulary")):
            vocabulary = parse_vocabulary(params.get("vocabulary"))
            _real_text_values(params, vocabulary)
    except VocabularyError as exc:
        return [exc.problem("/vocabulary")]
    except ValueError as exc:  # a definition that is not a recorded definition
        return [f"/definition: params_invalid: {str(exc).splitlines()[0]}"]
    return []


def _rendering(document: RuleSetDocument | None, form: str) -> Rendering | None:
    if document is None:
        return None
    return next((r for r in document.renderings if r.rendering == form), None)


def _decoy(document: RuleSetDocument | None, decoy_id: str) -> Decoy:
    found = (
        None if document is None else next((d for d in document.decoys if d.id == decoy_id), None)
    )
    if found is None:
        raise StepFailed(
            "decoy_not_declared",
            f"Kind {decoy_id!r} is a declared decoy, but "
            + (
                "the step pins no rule set"
                if document is None
                else f"rule set {document.name} declares no decoy {decoy_id!r}"
            )
            + ". Pin the rule set that declares it.",
            {"decoy": decoy_id, "next": NEXT_RULE_SET},
        )
    if not isinstance(found.generator, GenerateList):
        raise StepFailed(
            "decoy_not_runnable",
            f"Decoy {decoy_id!r} is generated by a "
            f"{next(iter(found.generator.model_dump(by_alias=True)))} generator, which this build "
            "cannot run yet (only a list). Declare its values as a list.",
            {"decoy": decoy_id, "next": NEXT_RULE_SET},
        )
    return found


def _band(vocabulary: Vocabulary, length: int) -> str | None:
    for band in vocabulary.length_bands:
        if length >= band.min_chars and (band.max_chars is None or length < band.max_chars):
            return band.name
    return None


class InsertElement:
    manifest = manifest(
        NAME,
        "mapper",
        "Puts the element into each carrier - filling the carrier's slot, or at a line, sentence "
        "or word boundary of real text - and records where (ins_start, ins_end, in code points), "
        "the element as inserted, the boundary, the rendering and the normaliser's verdict. "
        "Decoy kinds take a declared decoy; a framing phrase may go just before the element.",
        params={
            "mode": {
                "type": "string",
                "enum": [SLOT_MODE, REAL_TEXT_MODE],
                "default": SLOT_MODE,
                "title": "Mode",
                "x-hint": "slot: fill the carrier run's slot token; real_text: insert at a "
                "boundary of real text.",
            },
            "text_column": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$",
                "default": "text",
                "title": "Text column",
            },
            "element_column": {
                "type": "string",
                "minLength": 1,
                "title": "Element column",
                "x-hint": "The canonical element per row (synth_identifier's value, for example).",
            },
            "slot_column": {
                "type": "string",
                "minLength": 1,
                "default": "slot",
                "title": "Slot column",
            },
            "vocabulary": {
                "type": "string",
                "minLength": 2,
                "title": "Vocabulary",
                "x-widget": "textarea",
                "x-hint": '"@input" reads the input version\'s recorded vocabulary.',
            },
            "definition": {
                "type": "string",
                "minLength": 2,
                "title": "Behaviour definition",
                "x-widget": "textarea",
                "x-hint": 'real_text mode: "@input", or dataworks_resolve_behaviour\'s record.',
            },
            "real_text_values": {
                "type": "string",
                "default": "{}",
                "title": "Real-text plan values (JSON)",
                "x-widget": "textarea",
                "x-hint": "{genre, kind, framing, form} for real-text rows lacking those "
                "columns; each a declared value.",
            },
            "placements": {
                "type": "array",
                "items": {"type": "string", "enum": list(SLOTTED)},
                "minItems": 1,
                "uniqueItems": True,
                "title": "Real-text placements",
                "x-hint": "real_text mode: drawn (seeded) for a row with no placement column. "
                "Default: start, middle and end.",
            },
            "framing_phrase_share": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0,
                "title": "Share with a framing phrase",
                "x-unit": "fraction of rows",
                "x-hint": "A phrase from the vocabulary's phrase_list goes just before the "
                "element, outside the span.",
            },
            **PIN_SCHEMA,
        },
        required=("element_column", "vocabulary"),
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_insert_params(params)

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        mode = params.get("mode", SLOT_MODE)
        reads = [str(params.get("text_column") or "text"), str(params.get("element_column"))]
        writes = [name for name, _ in SPAN_COLUMNS]
        if mode == SLOT_MODE:
            reads += [str(params.get("slot_column") or "slot"), "form", "kind"]
        else:
            writes += list(PROVENANCE_COLUMNS)
        return reads, writes, []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            vocabulary = vocabulary_of(params)
            definition = _definition(params)
            fixed = _real_text_values(params, vocabulary)
        except VocabularyError as exc:
            raise refused(exc) from None
        mode = str(params.get("mode", SLOT_MODE))
        if mode == REAL_TEXT_MODE and definition is None:
            raise StepFailed(
                "input_not_recorded",
                "real_text mode records the behaviour key on each row: the step has no definition.",
                {"param": "definition", "next": NEXT_VERSION},
            )
        document = rule_set_of(params, self.manifest.ref_text)
        text_column = str(params.get("text_column") or "text")
        element_column = str(params["element_column"])
        slot_column = str(params.get("slot_column") or "slot")
        share = float(params.get("framing_phrase_share") or 0.0)
        names = batch.schema.names
        for needed in [text_column, element_column] + (
            [slot_column, "form", "kind"] if mode == SLOT_MODE else []
        ):
            if needed not in names:
                raise StepFailed(
                    "column_not_found",
                    f"insert_element@1 reads {needed!r}, which the input does not have.",
                    {"column": needed},
                )
        if mode == REAL_TEXT_MODE:
            lacking = [f for f in REAL_TEXT_VALUE_FIELDS if f not in names and f not in fixed]
            if lacking:
                raise StepFailed(
                    "params_invalid",
                    f"Real-text rows have no {lacking} column(s); give each in "
                    "real_text_values.",
                    {"fields": lacking},
                )
        rows = batch.to_pylist()
        content = ctx.content_columns
        out_rows: list[dict[str, Any]] = []
        events = []
        span: dict[str, list[Any]] = {name: [] for name, _ in SPAN_COLUMNS}
        provenance: dict[str, list[Any]] = {name: [] for name in PROVENANCE_COLUMNS}
        dropped: Counter[str] = Counter()
        rendered_forms: dict[str, str] = {}
        given_forms: set[str] = set()
        decoys: Counter[str] = Counter()
        slotted = [str(p) for p in (params.get("placements") or SLOTTED)]

        def drop(pair: tuple[str, int], code: str, reason: str, statistic: str, value: Any) -> None:
            dropped[code] += 1
            events.append(ctx.drop(pair, code, reason, statistic, value))

        for row in rows:
            pair = (str(row["_dw_row_key"]), int(row["_dw_occurrence"]))
            key = pair[0]
            text = row.get(text_column)
            if text is None:
                drop(
                    pair,
                    "no_carrier_text",
                    f"no {text_column!r}: not a carrier (a plan row, or text not written yet)",
                    f"{text_column}_length",
                    0.0,
                )
                continue
            text = str(text)
            values: dict[str, Any] = {}
            for field in REAL_TEXT_VALUE_FIELDS:
                value = row.get(field) if field in names else None
                values[field] = fixed.get(field) if value is None else value
            for field in ("kind", "form"):
                if values[field] not in vocabulary.values(field):
                    raise StepFailed(
                        "vocabulary_value_undeclared",
                        f"Row {key[:12]} has {field} = {values[field]!r}, which the vocabulary "
                        f"does not declare (declared: {list(vocabulary.values(field))}).",
                        {
                            "field": field,
                            "value": values[field],
                            "row_key": key,
                            "next": NEXT_VALIDATE,
                        },
                    )
            rnd = row_random(ctx.step_seed, NAME, *pair)
            # 1. the element: a declared decoy's (seeded), else the element column's
            decoy_id: str | None = None
            element: str | None
            if values["kind"] in vocabulary.decoys:
                decoy = _decoy(document, str(values["kind"]))
                assert isinstance(decoy.generator, GenerateList)
                element = decoy.generator.items[rnd.randrange(len(decoy.generator.items))]
                decoy_id = decoy.id
                decoys[decoy.id] += 1
            else:
                raw = row.get(element_column)
                element = None if raw is None else str(raw)
            # 2. the form: rendered by a declared rendering (and normalised back), else as given
            rendering = _rendering(document, str(values["form"]))
            recovered = False
            rendered = ""
            if element is not None:
                if rendering is not None:
                    spec = rendering.model_dump(by_alias=True)
                    try:
                        rendered = rendering_rules.render(spec, element)
                        recovered = rendering_rules.normalise(spec, rendered) == element
                    except rendering_rules.RenderingNotRunnable as exc:
                        raise StepFailed(
                            "rendering_not_runnable",
                            f"Rendering {rendering.rendering!r} cannot run in a build: {exc}.",
                            {"rendering": rendering.rendering, "next": NEXT_RULE_SET},
                        ) from None
                    rendered_forms[str(values["form"])] = rendering.rendering
                else:
                    rendered, recovered = element, True
                    given_forms.add(str(values["form"]))
            phrase = None
            if rendered and vocabulary.phrase_list and share > 0 and rnd.random() < share:
                phrase = vocabulary.phrase_list[rnd.randrange(len(vocabulary.phrase_list))]
            piece = (f"{phrase} " if phrase else "") + rendered
            # 3. where it goes
            new_text: str | None
            if mode == SLOT_MODE:
                token = row.get(slot_column)
                if token is None:
                    new_text, start, found_kind = None, None, None
                else:
                    count = text.count(str(token))
                    if count != 1:
                        drop(
                            pair,
                            "slot_missing" if count == 0 else "slot_repeated",
                            f"the carrier holds its slot token {count} times; exactly once is "
                            "needed",
                            slot_column,
                            float(count),
                        )
                        continue
                    at = text.index(str(token))
                    new_text = text[:at] + piece + text[at + len(str(token)) :]
                    start, found_kind = at, boundaries.SLOT
            else:
                placement = row.get("placement") if "placement" in names else None
                if placement not in SLOTTED:
                    placement = slotted[rnd.randrange(len(slotted))]
                if element is None:
                    new_text, start, found_kind = None, None, None
                else:
                    position, found_kind = boundaries.boundary(text, str(placement))
                    new_text, start = boundaries.insert_at(text, position, piece)
                assert definition is not None
                given = row.get("generator") if "generator" in names else None
                if given is None and row.get("_dw_source_id") is None:
                    # Review R3: a row with no source (a generated row) is not real text; its
                    # generator would read "real_text:None" (FR-017.12, FR-017.7).
                    raise StepFailed(
                        "real_text_without_source",
                        f"Row {key[:12]} has no source and no generator: real_text mode inserts "
                        "into imported text only (a generated row records its own generator).",
                        {"row_key": key, "next": NEXT_VERSION},
                    )
                version = definition["definition_version"]
                fills = {
                    "behaviour_key": definition["key"],
                    "definition_version": NOT_VERIFIED if version is None else str(version),
                    "generator": f"real_text:{row.get('_dw_source_id')}",
                    "carrier_id": key,
                    **values,
                    "placement": placement,
                    "length_band": _band(vocabulary, len(new_text if new_text else text)),
                }
                for name in PROVENANCE_COLUMNS:
                    current = row.get(name) if name in names else None
                    provenance[name].append(current if current is not None else fills[name])
            # 4. record it
            if new_text is None or start is None:
                out_rows.append(row)
                for name, _ in SPAN_COLUMNS:
                    span[name].append(
                        False if name in ("element_present", "element_recovered") else None
                    )
                continue
            ins_start = start + (len(phrase) + 1 if phrase else 0)
            ins_end = ins_start + len(rendered)
            changed = {**row, text_column: new_text}
            new_key = compute_row_key(
                {c: changed.get(c) for c in content}, content, ctx.rowkey_scheme
            )
            event = ctx.change(
                pair,
                new_key,
                "element_inserted",
                "the element was inserted"
                + (" (a declared decoy)" if decoy_id else "")
                + f" at a {found_kind} boundary",
                "ins_start",
                float(ins_start),
            )
            events.append(event)
            changed["_dw_row_key"] = new_key
            changed["_dw_occurrence"] = int(event.new_occurrence or 0)
            out_rows.append(changed)
            for name, value in (
                ("ins_start", ins_start),
                ("ins_end", ins_end),
                ("element_inserted", rendered),
                ("boundary_kind", found_kind),
                ("element_present", ins_end > ins_start),
                ("element_recovered", recovered and ins_end > ins_start),
                ("element_rendering", rendering.rendering if rendering is not None else None),
                (
                    "element_normaliser",
                    _normaliser_text(rendering) if rendering is not None else None,
                ),
                ("decoy_id", decoy_id),
                ("framing_phrase", phrase),
            ):
                span[name].append(value)
        output = (
            pa.Table.from_pylist(out_rows, schema=batch.schema) if out_rows else batch.slice(0, 0)
        )
        if mode == REAL_TEXT_MODE:
            for name in PROVENANCE_COLUMNS:
                array = pa.array(provenance[name], pa.string())
                if name in output.schema.names:
                    output = output.set_column(output.schema.get_field_index(name), name, array)
                else:
                    output = output.append_column(name, array)
        clash = [name for name, _ in SPAN_COLUMNS if name in output.schema.names]
        if clash:
            raise StepFailed(
                "output_column_exists",
                f"insert_element@1 writes {clash}, which the input already has.",
                {"columns": clash},
            )
        for name, type_ in SPAN_COLUMNS:
            output = output.append_column(name, pa.array(span[name], type_))
        written = [n for n, _ in SPAN_COLUMNS] + (
            list(PROVENANCE_COLUMNS) if mode == REAL_TEXT_MODE else []
        )
        return OperatorResult(
            output=output,
            events=events,
            output_roles={n: "metadata" for n in written if n not in content},
            report={
                "insert_element": {
                    "mode": mode,
                    "rows_inserted": sum(1 for v in span["element_present"] if v),
                    "dropped": dict(sorted(dropped.items())),
                    "forms_rendered": dict(sorted(rendered_forms.items())),
                    "forms_as_given": sorted(given_forms),
                    "decoys": dict(sorted(decoys.items())),
                    "rule_set": (
                        None
                        if document is None
                        else f"{document.name}@{params.get('rule_set_version')}"
                    ),
                }
            },
        )


def _normaliser_text(rendering: Rendering) -> str:
    spec = rendering.normaliser.model_dump(by_alias=True)
    kind = next(iter(spec))
    value = spec[kind]
    return f"{kind}:{value}" if not isinstance(value, dict) else f"{kind}:{value.get('id')}"
