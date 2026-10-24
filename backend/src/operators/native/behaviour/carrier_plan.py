"""``carrier_plan@1``: a behaviour's carrier plan over an index source (017 FR-017.12, FR-017.13,
FR-017.17, FR-017.21, FR-017.55; FTID §3.3; TD-017.1, TD-017.2, TD-017.7, TD-017.14).

A **mapper**: its rows stay ``origin = source``, so they are seed rows for a carrier generation
run and pass the generation guards unchanged (TD-017.7). The index source must hold exactly
``rows`` rows (``plan_size_mismatch`` otherwise).

Deterministic and seeded by the step seed: rows are taken in row-key order; the (genre, placement,
framing, length band) cells get exactly ``rows`` rows by largest-remainder allocation of the joint
planned shares, and ``kind``, ``form`` and each sub-question's planned answer get exact marginal
counts the same way; each list is shuffled with the step's generator before it is dealt. A
``templated_share`` of rows is marked ``generator = template`` (the existing synthesiser route,
FR-017.21); the other rows' generator is written by the carrier run.

Writes, as metadata: ``behaviour_key``, ``definition_version`` (the number, or ``not verified``),
``genre``, ``placement``, ``framing``, ``kind``, ``form``, ``length_band``, ``carrier_id`` (the
plan row's key), ``slot`` (``{{SLOT}}`` where the element goes: placement start, middle or end),
``generator`` and ``planned_<sub-question>``; and the empty text column the carrier run fills, as
content. Reports the census, ``behaviour_definition``, ``behaviour_configuration`` (finalize
records both on the version) and the split roles, with a DECLARED-EMPTY held-out split (finalize
writes it as a zero-row file, so a carrier run's held-out guard passes; FTID §7.3, I10).

The vocabulary, shares and definition are JSON text (FTID I14: the operator schema subset has no
nested objects, ``operators/schema_subset.py``), parsed by ``services/behaviour/vocabulary.py``.
No behaviour is named here: every value is the behaviour's data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
from pydantic import ValidationError

from ....schemas.behaviour_datasets import RecordedDefinitionIn
from ....services.behaviour import census as census_mod
from ....services.behaviour.vocabulary import (
    CELL_DIMENSIONS,
    Vocabulary,
    VocabularyError,
    joint_cells,
    largest_remainder,
    parse_shares,
    parse_vocabulary,
)
from ....services.row_keys import compute_row_key
from ...context import RunContext
from ...errors import StepFailed
from ...protocol import OperatorResult
from ..curation.common import manifest, read_all

SLOT = "{{SLOT}}"
#: Placements whose carrier holds the element's slot (``none``: no element; ``whole``: an
#: instructed carrier written as one text).
SLOTTED = frozenset({"start", "middle", "end"})
NOT_VERIFIED = "not verified"
TEMPLATE = census_mod.TEMPLATE
PLAN_COLUMNS: tuple[str, ...] = (
    "behaviour_key",
    "definition_version",
    "genre",
    "placement",
    "framing",
    "kind",
    "form",
    "length_band",
    "carrier_id",
    "slot",
    "generator",
)
NEXT_INDEX = {
    "tool": "dataworks_create_index_source",
    "args": {},
    "why": "make an index source of exactly the planned number of rows",
}


def _json_param(params: Mapping[str, Any], name: str, default: str) -> Any:
    raw = params.get(name, default)
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except ValueError as exc:
        raise VocabularyError(
            "vocabulary_invalid", f"{name} is not JSON: {exc}.", {"field": name}
        ) from None


def definition_of(params: Mapping[str, Any]) -> dict[str, Any]:
    """The resolved (or supplied) definition the plan records (FR-017.11)."""
    raw = _json_param(params, "definition", "null")
    try:
        parsed = RecordedDefinitionIn.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise VocabularyError(
            "vocabulary_invalid",
            f"definition is not a recorded definition ({where or 'definition'}: "
            f"{first.get('msg')}); pass dataworks_resolve_behaviour's record.",
            {"field": "definition"},
        ) from None
    return parsed.model_dump()


def configuration_of(params: Mapping[str, Any]) -> tuple[Vocabulary, dict[str, Any]]:
    """The parsed vocabulary and the configuration finalize records (FR-017.55)."""
    definition = definition_of(params)
    vocabulary = parse_vocabulary(params.get("vocabulary"))
    shares = parse_shares(params.get("shares"), vocabulary)
    templated_share = float(params.get("templated_share", 0.0))
    return vocabulary, {
        "behaviour_key": definition["key"],
        "vocabulary": vocabulary.as_dict(),
        "shares": shares,
        "rows": int(params["rows"]),
        "templated_share": templated_share,
        "templated_ceiling": float(params["templated_ceiling"]),
        "held_out_split_name": str(params.get("held_out_split_name", "test")),
        "text_column": str(params.get("text_column", "text")),
        "slot_placeholder": SLOT,
    }


def check_plan_params(params: Mapping[str, Any]) -> list[str]:
    """The registry's cross-parameter hook: a plan is refused when its recipe is validated."""
    for name, check in (
        ("/definition", lambda: definition_of(params)),
        ("/vocabulary", lambda: configuration_of(params)),
    ):
        try:
            check()
        except VocabularyError as exc:
            return [exc.problem(name)]
    return []


def _refuse(exc: VocabularyError) -> StepFailed:
    return StepFailed(exc.code, exc.message, exc.details)


def _deal(values: list[str], rng: np.random.Generator) -> list[str]:
    order = rng.permutation(len(values))
    return [values[i] for i in order]


def plan_rows(
    vocabulary: Vocabulary, configuration: Mapping[str, Any], n: int, rng: np.random.Generator
) -> dict[str, list[Any]]:
    """Every plan column for ``n`` rows in row-key order (pure given the generator)."""
    shares = configuration["shares"]
    cells: list[tuple[str, ...]] = []
    for cell, count in largest_remainder(joint_cells(shares, CELL_DIMENSIONS), n).items():
        cells.extend([cell] * count)
    cells = [cells[i] for i in rng.permutation(len(cells))]
    out: dict[str, list[Any]] = {d: [c[i] for c in cells] for i, d in enumerate(CELL_DIMENSIONS)}
    for dimension in ("kind", "form"):
        dealt: list[str] = []
        for value, count in largest_remainder(shares[dimension], n).items():
            dealt.extend([value] * count)
        out[dimension] = _deal(dealt, rng)
    for sq in vocabulary.sub_questions:
        dealt = []
        for value, count in largest_remainder(shares[f"sq:{sq.name}"], n).items():
            dealt.extend([value] * count)
        out[f"planned_{sq.name}"] = _deal(dealt, rng)
    templated = largest_remainder(
        {TEMPLATE: configuration["templated_share"], "model": 1 - configuration["templated_share"]},
        n,
    )[TEMPLATE]
    chosen = set(rng.choice(n, size=templated, replace=False).tolist()) if templated else set()
    out["generator"] = [TEMPLATE if i in chosen else None for i in range(n)]
    out["slot"] = [SLOT if p in SLOTTED else None for p in out["placement"]]
    return out


class CarrierPlan:
    manifest = manifest(
        "carrier_plan",
        "mapper",
        "Plans a behaviour's carriers over an index source: every row gets its genre, placement, "
        "framing, kind, form, length band, sub-question targets and slot, by exact seeded shares "
        "of the declared vocabulary; reports the census and records the definition and "
        "configuration on the version.",
        scope="dataset",
        params={
            "definition": {
                "type": "string",
                "minLength": 2,
                "title": "Behaviour definition (JSON)",
                "x-widget": "textarea",
                "x-hint": "dataworks_resolve_behaviour's record: {key, definition_version, "
                "positive_meaning, verified, source, read_at}.",
            },
            "vocabulary": {
                "type": "string",
                "minLength": 2,
                "title": "Vocabulary (JSON)",
                "x-widget": "textarea",
                "x-hint": "{genres, framings, kinds, forms, placements?, length_bands: [{name, "
                "min_chars, max_chars}], sub_questions?: [{name, values}], phrase_list?, "
                "decoys?}",
            },
            "shares": {
                "type": "string",
                "default": "{}",
                "title": "Planned shares (JSON)",
                "x-widget": "textarea",
                "x-hint": "{dimension: {value: share}} for genre, placement, framing, "
                "length_band, kind, form and sq:<name>; an omitted dimension is uniform.",
            },
            "rows": {"type": "integer", "minimum": 1, "maximum": 100_000, "title": "Rows (N)"},
            "templated_share": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0,
                "title": "Templated share",
                "x-hint": "Rows marked generator = template, filled by the synthesiser route.",
            },
            "templated_ceiling": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "title": "Templated ceiling",
                "x-hint": "The census flags a templated share above this.",
            },
            "held_out_split_name": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_.-]{1,64}$",
                "default": "test",
                "title": "Declared held-out split",
                "x-hint": "Written empty, so a carrier run's held-out guard passes.",
            },
            "text_column": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$",
                "default": "text",
                "title": "Carrier text column",
                "x-hint": "The content column a carrier run writes into.",
            },
        },
        required=("definition", "vocabulary", "rows", "templated_ceiling"),
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_plan_params(params)

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            definition = definition_of(params)
            vocabulary, configuration = configuration_of(params)
        except VocabularyError as exc:
            raise _refuse(exc) from None
        table = read_all(ctx)
        n = int(params["rows"])
        if table.num_rows != n:
            raise StepFailed(
                "plan_size_mismatch",
                f"The plan is for {n:,} rows; its input holds {table.num_rows:,}. Plan over an "
                "index source of exactly that many rows.",
                {"rows": n, "input_rows": table.num_rows, "next": NEXT_INDEX},
            )
        text_column = configuration["text_column"]
        planned = [f"planned_{sq.name}" for sq in vocabulary.sub_questions]
        clash = [c for c in (*PLAN_COLUMNS, *planned, text_column) if c in table.schema.names]
        if clash:
            raise StepFailed(
                "output_column_exists",
                f"carrier_plan@1 writes {clash}, which the input already has. Plan over a bare "
                "index source.",
                {"columns": clash},
            )
        keys = table.column("_dw_row_key").to_pylist()
        occurrences = table.column("_dw_occurrence").to_pylist()
        order = sorted(range(n), key=lambda i: (str(keys[i]), int(occurrences[i])))
        # The text column joins the content, so every plan row is keyed again over it (a changed
        # row, 002's rule): a later build re-keys over the same content and finds the same keys,
        # which the carriers' lineage names (``carrier_id`` = the new key).
        content = sorted({*ctx.content_columns, text_column})
        events = []
        new_keys: list[str] = []
        new_occurrences: list[int] = []
        for i, row in enumerate(table.select(sorted(ctx.content_columns)).to_pylist()):
            new_key = compute_row_key({**row, text_column: None}, content, ctx.rowkey_scheme)
            event = ctx.change(
                (str(keys[i]), int(occurrences[i])),
                new_key,
                "carrier_planned",
                f"planned as a carrier; the empty {text_column!r} column joined the content",
                "carrier_plan",
                text=text_column,
            )
            events.append(event)
            new_keys.append(new_key)
            new_occurrences.append(int(event.new_occurrence or 0))
        columns = plan_rows(vocabulary, configuration, n, ctx.rng)
        at = {row: position for position, row in enumerate(order)}

        def placed(values: list[Any]) -> list[Any]:
            return [values[at[i]] for i in range(n)]

        version = definition["definition_version"]
        built: dict[str, list[Any]] = {
            "behaviour_key": [definition["key"]] * n,
            "definition_version": [str(version) if version is not None else NOT_VERIFIED] * n,
            **{d: placed(columns[d]) for d in (*CELL_DIMENSIONS, "kind", "form")},
            "carrier_id": new_keys,
            "slot": placed(columns["slot"]),
            "generator": placed(columns["generator"]),
            **{c: placed(columns[c]) for c in planned},
        }
        output = table
        for name in (*PLAN_COLUMNS, *planned):
            output = output.append_column(name, pa.array(built[name], pa.string()))
        output = output.append_column(text_column, pa.array([None] * n, pa.string()))
        output = output.set_column(
            output.schema.get_field_index("_dw_row_key"),
            "_dw_row_key",
            pa.array(new_keys, output.schema.field("_dw_row_key").type),
        ).set_column(
            output.schema.get_field_index("_dw_occurrence"),
            "_dw_occurrence",
            pa.array(new_occurrences, output.schema.field("_dw_occurrence").type),
        )
        report_census = census_mod.census(configuration, built)
        splits = sorted({str(s) for s in table.column("_dw_split").to_pylist()})
        held_out = configuration["held_out_split_name"]
        roles: dict[str, Any] = {s: {"held_out": False} for s in splits if s != held_out}
        roles[held_out] = {"held_out": True, "declared_empty": True}
        return OperatorResult(
            output=output,
            events=events,
            output_roles={
                **dict.fromkeys((*PLAN_COLUMNS, *planned), "metadata"),
                text_column: "content",
            },
            report={
                "census": report_census,
                "behaviour_definition": definition,
                "behaviour_configuration": configuration,
                "split_roles": roles,
            },
        )
