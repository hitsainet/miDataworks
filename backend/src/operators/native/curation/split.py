"""``split``: assign every row to a named split, stratified, groups kept whole (FR-004.47–004.50).

A ``selector`` with the ``assign_split`` effect (003 ``effects.ALLOWED``): it writes ``_dw_split``,
emits one ``split_assigned`` event per row whose split changes, drops and changes nothing, and
declares ``split_roles`` in its report, which 003's executor copies into ``meta.json`` for 002's
finalize. The plan is the pure ``services/curation/split_plan.plan_split``.

Parameters are parallel arrays (``split_names``, ``split_fractions``, ``held_out``) rather than
FTDD §6.5's list of objects, because 003's supported schema subset has no arrays of objects (the
code wins; recorded). ``stratify_by`` defaults are resolved by the caller from the draft (the label
column plus an upstream ``cell_balancer``'s column, FR-004.41); the operator never guesses them.
Mode ``keep_source`` keeps the rows' existing splits and only declares their roles (Humicroedit's
upstream train/validation/test).

``split@1.1.0`` (:class:`SplitV11`, feature 017 FR-017.1 - FR-017.3; FTID 017 section 3.2) adds
**value-directed held-out splits**: rows whose value in a directing column is held out go first to
that column's held-out split (one per column, plus one for rows held out on more than one), whole
groups with them, over the connected components of every ``group_columns`` entry; the rest is split
by fraction as before. Each directed split is proved disjoint from training before the step
writes anything (``held_out_proof.prove``; refused ``held_out_not_disjoint`` /
``held_out_value_null``), and its role reports ``directed_by`` for finalize, which proves it again
over the written files. 1.1.0 is registered AFTER 1.0.0 (``current_version`` is insertion-ordered,
FTID I3); with none of the new parameters it assigns byte-identically to 1.0.0.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from ....core.config import get_settings
from ....rules.next_step import next_for
from ....services.curation import held_out_proof
from ....services.curation.binning import NULL_LABEL
from ....services.curation.held_out_proof import CITATION, DirectedBy, Facts, count_combos, prove
from ....services.curation.split_plan import (
    SplitPlan,
    SplitRefusal,
    SplitSpec,
    check_specs,
    plan_split,
)
from ...context import RunContext
from ...protocol import OperatorResult
from .common import key_order, manifest, param, read_all_or_empty, refuse, text_column

SPLIT = "_dw_split"
ORIGIN = "_dw_origin"


def specs_of(params: Mapping[str, Any]) -> list[SplitSpec]:
    names = list(param(params, "split_names", ["train", "test"]))
    fractions = list(param(params, "split_fractions", [0.9, 0.1]))
    held = set(param(params, "held_out", ["test"]))
    mode = param(params, "mode", "reassign")
    if mode == "reassign" and len(fractions) != len(names):
        raise refuse(
            "split_fractions_invalid",
            f"{len(names)} split names but {len(fractions)} fractions; give one fraction per split.",
            {"split_names": names, "split_fractions": fractions},
        )
    unknown = sorted(held - set(names))
    if unknown:
        raise refuse(
            "split_fractions_invalid",
            f"Held-out split {unknown[0]!r} is not one of the named splits.",
            {"held_out": sorted(held), "split_names": names},
        )
    if mode != "reassign":
        return [SplitSpec(n, 0.0, n in held) for n in names]
    return [SplitSpec(n, float(f), n in held) for n, f in zip(names, fractions, strict=True)]


class Split:
    manifest = manifest(
        "split",
        "selector",
        "Assigns every row to a named split, stratified by columns you choose, with whole groups "
        "kept together; generated rows never go to a held-out split.",
        scope="dataset",
        params={
            "split_names": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
                "minItems": 2,
                "default": ["train", "test"],
                "title": "Splits",
                "x-hint": "The first split takes whatever the others do not.",
            },
            "split_fractions": {
                "type": "array",
                "items": {"type": "number", "minimum": 0, "maximum": 1},
                "default": [0.9, 0.1],
                "title": "Fractions",
                "x-hint": "One per split, adding up to 1.",
            },
            "held_out": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["test"],
                "title": "Held-out splits",
                "x-hint": "Generated rows are never placed in these.",
            },
            "stratify_by": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "title": "Stratify by",
                "x-hint": "Each split keeps every stratum's share; default: label and the "
                "balanced column.",
            },
            "group_column": {
                "type": "string",
                "minLength": 1,
                "title": "Group column",
                "x-hint": "Rows sharing a value stay in one split (two edits of one headline).",
                "x-advanced": True,
            },
            "mode": {
                "type": "string",
                "enum": ["reassign", "keep_source"],
                "default": "reassign",
                "title": "Mode",
            },
        },
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        specs = specs_of(params)
        mode = param(params, "mode", "reassign")
        stratify = list(param(params, "stratify_by", []))
        group_column = params.get("group_column")
        try:
            if mode == "reassign":
                check_specs(specs)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        table = read_all_or_empty(ctx, batch)
        if table.num_rows:
            table = key_order(table)
        n = table.num_rows
        current = (
            [None if v is None else str(v) for v in table.column(SPLIT).to_pylist()]
            if SPLIT in table.schema.names
            else [None] * n
        )
        origins = (
            [None if v is None else str(v) for v in table.column(ORIGIN).to_pylist()]
            if ORIGIN in table.schema.names
            else [None] * n
        )
        held = {s.name for s in specs if s.held_out}
        roles = {
            s.name: {
                "held_out": s.held_out,
                "fraction": s.fraction if mode == "reassign" else None,
                "stratify_by": stratify,
                "group_column": group_column,
            }
            for s in specs
        }
        if mode == "keep_source":
            generated = [i for i in range(n) if origins[i] == "generated" and current[i] in held]
            if generated:
                raise refuse(
                    "generated_in_held_out",
                    f"{len(generated)} generated row(s) sit in a held-out split. A held-out split "
                    "holds only rows from sources (FR-002.31); only a value-directed held-out "
                    f"split proved disjoint from training may hold them ({CITATION}).",
                    {"rows": generated[:20], "rule": CITATION},
                )
            counts: dict[str, int] = {}
            for name in current:
                counts[str(name)] = counts.get(str(name), 0) + 1
            report = {"split_roles": roles, "mode": mode, "counts_by_split": counts}
            return OperatorResult(output=table, report=report)
        strata_columns = [text_column(table, c) for c in stratify]
        strata = [
            "|".join(str(col[i]) if col[i] is not None else NULL_LABEL for col in strata_columns)
            or "all"
            for i in range(n)
        ]
        groups = None
        if group_column:
            groups = [None if v is None else str(v) for v in text_column(table, str(group_column))]
        try:
            plan = plan_split(strata, groups, origins, specs, ctx.step_seed)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        names = plan.names()
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        events = [
            ctx.assign_split(
                (keys[i], occ[i]),
                names[i],
                "split_assigned",
                f"stratum {strata[i]} drawn to {names[i]}",
            )
            for i in range(n)
            if current[i] != names[i]
        ]
        column = pa.array(names, pa.string())
        if SPLIT in table.schema.names:
            output = table.set_column(table.schema.get_field_index(SPLIT), SPLIT, column)
        else:
            output = table.append_column(SPLIT, column)
        report = {
            "split_roles": roles,
            "mode": mode,
            "counts_by_split_and_stratum": plan.counts,
            "shortfalls": plan.shortfalls,
            "small_strata": plan.small_strata,
            "group_column": group_column,
        }
        return OperatorResult(output=output, events=events, report=report)


# --- split@1.1.0: value-directed held-out splits (feature 017) ---------------------------------

#: The runtime key capping the held-out values of one directing column (FR-017.54).
HELD_OUT_CAP_SETTING = "BEHAVIOUR_MAX_HELD_OUT_VALUES"


def _next_validate(why: str) -> dict[str, Any]:
    return next_for("dataworks_validate_recipe", {}, why)


@dataclass(frozen=True)
class Directed:
    """The directing parameters of one ``split@1.1.0`` step, checked."""

    columns: tuple[str, ...]
    held_out_values: dict[str, tuple[str, ...]]
    identity_columns: tuple[str, ...]
    #: Directing column -> its held-out split; "" -> the split for rows held out on more than one.
    split_of: dict[str, str]
    group_columns: tuple[str, ...]

    def role(self, split: str) -> DirectedBy:
        columns = (
            self.columns
            if split == self.split_of.get("")
            else tuple(c for c in self.columns if self.split_of[c] == split)
        )
        return DirectedBy(
            columns=columns,
            all_columns=self.columns,
            held_out_values=self.held_out_values,
            identity_columns=self.identity_columns,
            group_columns=self.group_columns,
        )

    def splits(self) -> list[str]:
        return list(dict.fromkeys(self.split_of.values()))


def group_columns_of(params: Mapping[str, Any]) -> list[str]:
    """``group_columns`` with the 1.0.0 ``group_column`` folded in (first, once)."""
    out = [str(params["group_column"])] if params.get("group_column") else []
    for c in param(params, "group_columns", []):
        if str(c) not in out:
            out.append(str(c))
    return out


def directed_of(params: Mapping[str, Any]) -> Directed | None:
    """The checked directing parameters, or None when the step directs nothing.

    Raises :class:`SplitRefusal`: ``directed_split_name_conflict`` (a directed split that is not a
    held-out split of this step, or that a fraction also fills), ``cap_exceeded`` (naming
    ``BEHAVIOUR_MAX_HELD_OUT_VALUES``), ``split_fractions_invalid`` (malformed parallel arrays)."""
    columns = [str(c) for c in param(params, "directing_columns", [])]
    if not columns:
        stray = [
            k
            for k in ("held_out_values", "held_out_value_columns", "held_out_split_names")
            if params.get(k)
        ]
        if stray:
            raise SplitRefusal(
                "split_fractions_invalid",
                f"{stray[0]} is set but no directing column is named; name the directing columns.",
                {"parameter": stray[0], "next": _next_validate("name the directing columns")},
            )
        return None
    if len(set(columns)) != len(columns):
        raise SplitRefusal(
            "split_fractions_invalid",
            "Each directing column may be named once.",
            {"directing_columns": columns, "next": _next_validate("name each column once")},
        )
    if param(params, "mode", "reassign") != "reassign":
        raise SplitRefusal(
            "directed_split_name_conflict",
            "Value-directed held-out splits need mode 'reassign' (keep_source keeps the rows' "
            "existing splits).",
            {"mode": params.get("mode"), "next": _next_validate("use mode reassign")},
        )
    values = [str(v) for v in param(params, "held_out_values", [])]
    value_columns = [str(c) for c in param(params, "held_out_value_columns", [])]
    if len(values) != len(value_columns):
        raise SplitRefusal(
            "split_fractions_invalid",
            f"{len(values)} held-out values but {len(value_columns)} held-out value columns; "
            "give one column per value (parallel lists).",
            {"next": _next_validate("pair each held-out value with its column")},
        )
    held: dict[str, list[str]] = {c: [] for c in columns}
    for c, v in zip(value_columns, values, strict=True):
        if c not in held:
            raise SplitRefusal(
                "split_fractions_invalid",
                f"Held-out value {v!r} names column {c!r}, which is not a directing column.",
                {"column": c, "value": v, "next": _next_validate("name a directing column")},
            )
        if v not in held[c]:
            held[c].append(v)
    cap = int(get_settings().behaviour_max_held_out_values)
    for c in columns:
        if not held[c]:
            raise SplitRefusal(
                "split_fractions_invalid",
                f"Directing column {c!r} has no held-out value; give at least one.",
                {"column": c, "next": _next_validate("give the column a held-out value")},
            )
        if len(held[c]) > cap:
            raise SplitRefusal(
                "cap_exceeded",
                f"Directing column {c!r} holds out {len(held[c])} values; "
                f"{HELD_OUT_CAP_SETTING} allows {cap}.",
                {
                    "setting": HELD_OUT_CAP_SETTING,
                    "limit": cap,
                    "actual": len(held[c]),
                    "column": c,
                    "next": next_for("dataworks_get_settings", {}, f"read {HELD_OUT_CAP_SETTING}"),
                },
            )
    identity = [str(c) for c in param(params, "identity_columns", [])]
    unknown = [c for c in identity if c not in columns]
    if unknown:
        raise SplitRefusal(
            "split_fractions_invalid",
            f"Identity column {unknown[0]!r} is not a directing column.",
            {"column": unknown[0], "next": _next_validate("name a directing column")},
        )
    names = [str(n) for n in param(params, "held_out_split_names", [])]
    expected = len(columns) + (1 if len(columns) > 1 else 0)
    if len(names) != expected:
        raise SplitRefusal(
            "directed_split_name_conflict",
            f"{len(columns)} directing column(s) need {expected} held-out split name(s) (one "
            "per column"
            + (", then one for rows held out on more than one" if expected > 1 else "")
            + f"); {len(names)} given.",
            {"held_out_split_names": names, "next": _next_validate("name the held-out splits")},
        )
    if len(set(names)) != len(names):
        raise SplitRefusal(
            "directed_split_name_conflict",
            "Each directing column needs its own held-out split.",
            {"held_out_split_names": names, "next": _next_validate("name distinct splits")},
        )
    split_names = [str(n) for n in param(params, "split_names", ["train", "test"])]
    fractions = list(param(params, "split_fractions", [0.9, 0.1]))
    held_names = set(param(params, "held_out", ["test"]))
    for name in names:
        if name not in split_names or name not in held_names:
            raise SplitRefusal(
                "directed_split_name_conflict",
                f"Directed split {name!r} must be one of the named splits and marked held out.",
                {"split": name, "next": _next_validate("name and hold out the split")},
            )
        i = split_names.index(name)
        if i < len(fractions) and float(fractions[i]) != 0.0:
            raise SplitRefusal(
                "directed_split_name_conflict",
                f"Directed split {name!r} has fraction {fractions[i]}; a directed split takes "
                "rows by their values only, so its fraction is 0.",
                {"split": name, "next": _next_validate("set the directed split's fraction to 0")},
            )
        if i == 0:
            raise SplitRefusal(
                "directed_split_name_conflict",
                f"Directed split {name!r} is the first split, which takes the rows no fraction "
                "draws; put a training split first.",
                {"split": name, "next": _next_validate("put a training split first")},
            )
    split_of = dict(zip(columns, names, strict=False))
    if len(columns) > 1:
        split_of[""] = names[-1]
    return Directed(
        columns=tuple(columns),
        held_out_values={c: tuple(held[c]) for c in columns},
        identity_columns=tuple(identity),
        split_of=split_of,
        group_columns=tuple(group_columns_of(params)),
    )


def directed_targets(
    directed: Directed, values: Sequence[Sequence[str | None]], index: Mapping[str, int]
) -> list[int | None]:
    """Per row: the index of the held-out split its directing values send it to, or None."""
    probe = directed.role(directed.splits()[0])
    out: list[int | None] = []
    for combo in zip(*values, strict=True):
        held = probe.held_columns(combo)
        if not held:
            out.append(None)
        elif len(held) == 1:
            out.append(index[directed.split_of[held[0]]])
        else:
            out.append(index[directed.split_of[""]])
    return out


def proofs_of(
    directed: Directed,
    plan: SplitPlan,
    values: Sequence[Sequence[str | None]],
    groups: Mapping[str, Sequence[str | None]],
    origins: Sequence[str | None],
) -> dict[str, held_out_proof.Proof]:
    """The proof of every directed split over this step's own assignment (the fast refusal)."""
    names = plan.names()
    n = len(names)
    held_names = {s.name for s in plan.specs if s.held_out}
    training = [i for i in range(n) if names[i] not in held_names]
    out: dict[str, held_out_proof.Proof] = {}
    for split in directed.splits():
        rows = [i for i in range(n) if names[i] == split]
        role = directed.role(split)
        shared = {
            g: len(
                {groups[g][i] for i in rows if groups[g][i] is not None}
                & {groups[g][i] for i in training if groups[g][i] is not None}
            )
            for g in directed.group_columns
        }
        facts = Facts(
            held_out_rows=count_combos(values, rows),
            training_rows=count_combos(values, training),
            shared_groups=shared,
            generated_rows=sum(1 for i in rows if origins[i] == "generated"),
            training_splits=tuple(s.name for s in plan.specs if not s.held_out),
        )
        out[split] = prove(split, role, facts)
    return out


def check_directed_params(params: Mapping[str, Any]) -> list[str]:
    """The registry's cross-parameter hook: a recipe is refused when saved, not at its build."""
    try:
        directed_of(params)
    except SplitRefusal as exc:
        return [f"/directing_columns: {exc.code}: {exc.message}"]
    return []


class SplitV11(Split):
    manifest = manifest(
        "split",
        "selector",
        "Assigns every row to a named split, stratified by columns you choose, with whole groups "
        "kept together; rows whose directing value is held out go to their held-out split first, "
        "proved absent from training; generated rows never go to any other held-out split.",
        scope="dataset",
        version="1.1.0",
        params={
            **Split.manifest.params_schema["properties"],
            "group_columns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "default": [],
                "title": "Group columns",
                "x-hint": "Rows sharing a value in ANY of these stay in one split, transitively "
                "(carrier and pair).",
                "x-advanced": True,
            },
            "directing_columns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "default": [],
                "title": "Directing columns",
                "x-hint": "Columns whose held-out values choose a held-out split (genre, "
                "generator, any column).",
                "x-advanced": True,
            },
            "held_out_values": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "title": "Held-out values",
                "x-hint": "One entry per value; its column is the same position of Held-out "
                "value columns.",
                "x-advanced": True,
            },
            "held_out_value_columns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "default": [],
                "title": "Held-out value columns",
                "x-advanced": True,
            },
            "identity_columns": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "default": [],
                "title": "Identity columns",
                "x-hint": "Directing columns compared without a revision suffix (a model id).",
                "x-advanced": True,
            },
            "held_out_split_names": {
                "type": "array",
                "items": {"type": "string", "pattern": "^[A-Za-z0-9_.-]{1,64}$"},
                "default": [],
                "title": "Held-out split per directing column",
                "x-hint": "One per directing column, then one for rows held out on more than one.",
                "x-advanced": True,
            },
        },
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_directed_params(params)

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            directed = directed_of(params)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        groups = group_columns_of(params)
        if directed is None and (
            len(groups) <= 1 or param(params, "mode", "reassign") != "reassign"
        ):
            # Nothing new declared: exactly 1.0.0 (golden test). A single group named through
            # ``group_columns`` alone is 1.0.0's ``group_column`` (Review R1 F1: 1.0.0 reads only
            # ``group_column``, so passing ``params`` unchanged dropped the group and let carriers
            # span train and test). keep_source keeps the rows' splits; groups do not apply.
            if groups and not params.get("group_column"):
                params = {**params, "group_column": groups[0]}
            result = super().run(batch, params, ctx)
            return result
        return self._run_directed(batch, params, ctx, directed, groups)

    def _run_directed(
        self,
        batch: pa.Table,
        params: Mapping[str, Any],
        ctx: RunContext,
        directed: Directed | None,
        group_names: list[str],
    ) -> OperatorResult:
        specs = specs_of(params)
        stratify = list(param(params, "stratify_by", []))
        try:
            check_specs(specs)
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        table = read_all_or_empty(ctx, batch)
        if table.num_rows:
            table = key_order(table)
        n = table.num_rows
        current = (
            [None if v is None else str(v) for v in table.column(SPLIT).to_pylist()]
            if SPLIT in table.schema.names
            else [None] * n
        )
        origins = (
            [None if v is None else str(v) for v in table.column(ORIGIN).to_pylist()]
            if ORIGIN in table.schema.names
            else [None] * n
        )
        strata_columns = [text_column(table, c) for c in stratify]
        strata = [
            "|".join(str(col[i]) if col[i] is not None else NULL_LABEL for col in strata_columns)
            or "all"
            for i in range(n)
        ]
        groups = {g: text_column(table, g) for g in group_names}
        index = {s.name: i for i, s in enumerate(specs)}
        values: list[list[str | None]] = []
        targets: list[int | None] | None = None
        both: int | None = None
        if directed is not None:
            values = [text_column(table, c) for c in directed.columns]
            targets = directed_targets(directed, values, index)
            if "" in directed.split_of:
                both = index[directed.split_of[""]]
        try:
            plan = plan_split(
                strata,
                None,
                origins,
                specs,
                ctx.step_seed,
                group_columns=[groups[g] for g in group_names],
                directed=targets,
                both=both,
            )
        except SplitRefusal as exc:
            raise refuse(exc.code, exc.message, exc.details) from None
        roles: dict[str, dict[str, Any]] = {
            s.name: {
                "held_out": s.held_out,
                "fraction": s.fraction,
                "stratify_by": stratify,
                "group_column": group_names[0] if group_names else None,
                "group_columns": group_names,
            }
            for s in specs
        }
        proofs: dict[str, held_out_proof.Proof] = {}
        if directed is not None:
            # A directed held-out split is proved disjoint before anything is written.
            proofs = proofs_of(directed, plan, values, groups, origins)
            for split, proof in proofs.items():
                if not proof.passes:
                    code, message, details = proof.refusal()
                    raise refuse(code, message, details)
                roles[split]["directed_by"] = directed.role(split).as_dict()
        names = plan.names()
        keys = table.column("_dw_row_key").to_pylist()
        occ = table.column("_dw_occurrence").to_pylist()
        events = [
            ctx.assign_split(
                (keys[i], occ[i]),
                names[i],
                "split_assigned",
                (
                    f"held out by value to {names[i]} ({CITATION})"
                    if targets is not None and names[i] in proofs and targets[i] is not None
                    else f"stratum {strata[i]} drawn to {names[i]}"
                ),
            )
            for i in range(n)
            if current[i] != names[i]
        ]
        column = pa.array(names, pa.string())
        if SPLIT in table.schema.names:
            output = table.set_column(table.schema.get_field_index(SPLIT), SPLIT, column)
        else:
            output = table.append_column(SPLIT, column)
        report = {
            "split_roles": roles,
            "mode": "reassign",
            "counts_by_split_and_stratum": plan.counts,
            "shortfalls": plan.shortfalls,
            "small_strata": plan.small_strata,
            "group_column": group_names[0] if group_names else None,
            "group_columns": group_names,
            "largest_unit": plan.largest_unit,
            "directed_rows": plan.directed_rows,
            "held_out_proofs": {k: p.as_dict() for k, p in proofs.items()},
        }
        return OperatorResult(output=output, events=events, report=report)
