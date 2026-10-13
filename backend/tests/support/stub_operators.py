"""Stub operators and a stub registry obeying FR-003.3, for feature 002's tests (FTID 002 IQ1).

They live in ``tests/support/`` only: production installs feature 003's registry, and until then
``NoOperatorsInstalled`` refuses every operator. The stub executor is a small, honest stand-in for
003's ``execute_step``: it reads the input parts, runs one operator, writes ``part-*.parquet``,
``events.parquet`` and ``meta.json`` under staging and renames them into place, then leaves the link
callback to the test driver (``drive_build``), mirroring Celery's ``link`` without a broker.

Several stubs are deliberately WRONG — ``stub_bad_drop`` (no reason), ``stub_lose_row`` (no event),
``stub_wrong_key`` (reports a key the content does not hash to), ``stub_unseeded`` (ignores the step
seed) — so feature 002's checks have something real to catch.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.storage import resolve_under_data_dir, staging_dir
from src.services.operator_port import OperatorInfo, OperatorRefusal, StepSpec
from src.services.row_keys import compute_row_key
from src.services.step_contract import EVENT_SCHEMA, EVENTS_FILE, META_FILE

Row = dict[str, Any]


@dataclass
class Outcome:
    rows: list[Row]
    events: list[dict[str, Any]] = field(default_factory=list)
    split_roles: dict[str, Any] | None = None
    output_roles: dict[str, str] | None = None
    #: Override the counts reported in meta.json (to test the accounting check).
    meta_override: dict[str, Any] = field(default_factory=dict)


@dataclass
class Context:
    seed: int
    roles: dict[str, str]
    scheme: str
    bindings: list[dict[str, Any]]
    registry: StubRegistry

    def content_columns(self) -> list[str]:
        return sorted(c for c, r in self.roles.items() if r == "content")

    def key(self, row: Row) -> str:
        return compute_row_key(row, self.content_columns(), self.scheme)


def _event(kind: str, row: Row, reason_code: str, reason: str, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = dict.fromkeys(EVENT_SCHEMA.names)
    event.update(
        kind=kind,
        row_key=row["_dw_row_key"],
        occurrence=row["_dw_occurrence"],
        reason_code=reason_code,
        reason=reason,
    )
    event.update(extra)
    return event


def _assign_occurrences(rows: list[Row], fresh: set[int]) -> None:
    """Give rows at indexes in ``fresh`` the next occurrence not used by any other row's key."""
    used: dict[str, set[int]] = {}
    for i, row in enumerate(rows):
        if i not in fresh:
            used.setdefault(row["_dw_row_key"], set()).add(row["_dw_occurrence"])
    for i in sorted(fresh):
        taken = used.setdefault(rows[i]["_dw_row_key"], set())
        occurrence = 0
        while occurrence in taken:
            occurrence += 1
        rows[i]["_dw_occurrence"] = occurrence
        taken.add(occurrence)


# --- the operators ----------------------------------------------------------------------------


def op_keep(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    return Outcome(rows)


def op_drop_short(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    column, minimum = params.get("column", "text"), params["min_len"]
    kept, events = [], []
    for row in rows:
        length = len(row[column] or "")
        if length < minimum:
            events.append(
                _event(
                    "dropped",
                    row,
                    "too_short",
                    f"{column} has {length} characters, below {minimum}",
                    statistic_name="length",
                    statistic_value=float(length),
                    threshold=json.dumps({"value": minimum, "comparator": "<"}),
                )
            )
        else:
            kept.append(row)
    return Outcome(kept, events)


def op_band(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    column, low, high = params.get("column", "score"), params["min"], params["max"]
    kept, events = [], []
    for row in rows:
        value = float(row[column])
        if low <= value <= high:
            events.append(
                _event(
                    "dropped",
                    row,
                    "inside_band",
                    f"{column} {value:.2f} is inside the uncertain band [{low}, {high}]",
                    statistic_name=column,
                    statistic_value=value,
                    threshold=json.dumps({"value": [low, high], "comparator": "between"}),
                )
            )
        else:
            kept.append(row)
    return Outcome(kept, events)


def op_balance(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """Downsample every class to the smallest class's size, seeded by the step seed."""
    column = params.get("column", "label")
    counts = Counter(row[column] for row in rows)
    target = min(counts.values()) if counts else 0
    rng = np.random.default_rng(ctx.seed)
    drop: set[int] = set()
    for value, n in counts.items():
        indexes = [i for i, row in enumerate(rows) if row[column] == value]
        if n > target:
            drop.update(int(i) for i in rng.choice(indexes, size=n - target, replace=False))
    kept, events = [], []
    for i, row in enumerate(rows):
        if i in drop:
            events.append(
                _event(
                    "dropped",
                    row,
                    "balance",
                    f"downsampled class {row[column]!r} to {target} rows (seed {ctx.seed})",
                    statistic_name="class_count",
                    statistic_value=float(counts[row[column]]),
                )
            )
        else:
            kept.append(row)
    return Outcome(kept, events)


def op_upper(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    column = params.get("column", "text")
    out: list[Row] = []
    events: list[dict[str, Any]] = []
    fresh: set[int] = set()
    pending: list[tuple[int, Row, Row]] = []
    for row in rows:
        new = dict(row)
        new[column] = (row[column] or "").upper()
        if new[column] == row[column]:
            out.append(row)
            continue
        new["_dw_row_key"] = ctx.key(new)
        fresh.add(len(out))
        pending.append((len(out), row, new))
        out.append(new)
    _assign_occurrences(out, fresh)
    for _, old, new in pending:
        events.append(
            _event(
                "changed",
                old,
                "upper_cased",
                f"{column} upper-cased",
                new_row_key=new["_dw_row_key"],
                new_occurrence=new["_dw_occurrence"],
                statistic_name="length",
                statistic_value=float(len(new[column])),
            )
        )
    return Outcome(out, events)


def op_meta_tag(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """Change a metadata column only: the key is unchanged, the event is still `changed`."""
    column, value = params.get("column", "note"), params.get("value", "tagged")
    out, events = [], []
    for row in rows:
        new = dict(row)
        new[column] = value
        out.append(new)
        events.append(
            _event(
                "changed",
                row,
                "tagged",
                f"{column} set to {value!r}",
                new_row_key=row["_dw_row_key"],
                new_occurrence=row["_dw_occurrence"],
                statistic_name="tag",
                statistic_text=value,
            )
        )
    roles = dict(ctx.roles)
    roles.setdefault(column, "metadata")
    return Outcome(out, events, output_roles=roles)


def op_generate(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    count, column = params.get("count", 1), params.get("column", "text")
    out = list(rows)
    events: list[dict[str, Any]] = []
    fresh: set[int] = set()
    for row in rows[:count]:
        new = dict(row)
        new[column] = f"{row[column]} (generated)"
        new["_dw_origin"] = "generated"
        new["_dw_source_id"] = None
        new["_dw_source_locator"] = None
        new["_dw_parent_keys"] = [row["_dw_row_key"]]
        new["_dw_row_key"] = ctx.key(new)
        fresh.add(len(out))
        out.append(new)
    _assign_occurrences(out, fresh)
    for i in sorted(fresh):
        new = out[i]
        events.append(
            _event(
                "added",
                new,
                "generated",
                "paraphrase of its parent",
                parent_keys=new["_dw_parent_keys"],
            )
        )
    return Outcome(out, events)


def op_split(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """Assign every row to train or a held-out split, by a seeded hash of its key."""
    name, fraction = params.get("held_out", "test"), params.get("fraction", 0.25)
    exact: set[int] | None = None
    if "count" in params:  # hold out exactly `count` rows, chosen with the step seed
        order = list(np.random.default_rng(ctx.seed).permutation(len(rows)))
        exact = {int(i) for i in order[: params["count"]]}
    out, events = [], []
    for index, row in enumerate(rows):
        bucket = random.Random(f"{ctx.seed}:{row['_dw_row_key']}:{row['_dw_occurrence']}").random()
        held = index in exact if exact is not None else bucket < fraction
        split = name if held else "train"
        new = dict(row)
        new["_dw_split"] = split
        out.append(new)
        events.append(_event("split_assigned", row, "split", f"assigned to {split}", split=split))
    return Outcome(
        out, events, split_roles={"train": {"held_out": False}, name: {"held_out": True}}
    )


def op_bad_drop(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """WRONG ON PURPOSE: drops the first row with no reason and no statistic."""
    if not rows:
        return Outcome(rows)
    return Outcome(rows[1:], [_event("dropped", rows[0], "", "")])


def op_lose_row(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """WRONG ON PURPOSE: removes the first row and emits no event at all."""
    return Outcome(rows[1:])


def op_wrong_key(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """WRONG ON PURPOSE: changes content and reports a key the new content does not hash to."""
    out, events = [], []
    for row in rows:
        new = dict(row)
        new["text"] = (row["text"] or "") + "!"
        new["_dw_row_key"] = uuid.uuid4().hex + uuid.uuid4().hex
        new["_dw_occurrence"] = 0
        out.append(new)
        events.append(
            _event(
                "changed",
                row,
                "exclaimed",
                "added an exclamation mark",
                new_row_key=new["_dw_row_key"],
                new_occurrence=0,
                statistic_name="length",
                statistic_value=1.0,
            )
        )
    return Outcome(out, events)


def op_unseeded(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """WRONG ON PURPOSE: samples with an unseeded RNG, so a rebuild differs."""
    keep = max(1, len(rows) // 2)
    chosen = set(random.SystemRandom().sample(range(len(rows)), keep)) if rows else set()
    kept, events = [], []
    for i, row in enumerate(rows):
        if i in chosen:
            kept.append(row)
        else:
            events.append(
                _event(
                    "dropped",
                    row,
                    "sampled_out",
                    "not sampled",
                    statistic_name="rank",
                    statistic_value=float(i),
                )
            )
    return Outcome(kept, events)


def op_labeler(rows: list[Row], params: dict[str, Any], ctx: Context) -> Outcome:
    """A labeler that looks labels up by (fingerprint, row key) and calls the 'endpoint' only on a
    miss, counting calls (FR-002.29, FR-002.40)."""
    fingerprint, column = params.get("fingerprint", "fp-humor-v1"), params.get(
        "column", "label_humor"
    )
    store = ctx.registry.label_store
    out = []
    for row in rows:
        key = (fingerprint, row["_dw_row_key"])
        if key not in store:
            ctx.registry.endpoint_calls += 1
            store[key] = float(len(row.get("text") or "") % 7) / 7.0
        new = dict(row)
        new[column] = store[key]
        out.append(new)
    roles = dict(ctx.roles)
    roles[column] = "metadata"
    return Outcome(out, output_roles=roles)


@dataclass(frozen=True)
class StubOperator:
    info: OperatorInfo
    run: Callable[[list[Row], dict[str, Any], Context], Outcome]
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})


def _info(name: str, kind: str, **extra: Any) -> OperatorInfo:
    digest = hashlib.sha256(f"manifest:{name}@1".encode()).hexdigest()
    return OperatorInfo(name=name, version="1", manifest_hash=digest, kind=kind, **extra)


STUBS: dict[str, StubOperator] = {
    s.info.name: s
    for s in [
        StubOperator(_info("stub_keep", "filter"), op_keep),
        StubOperator(
            _info("stub_drop_short", "filter", input_columns=("text",)),
            op_drop_short,
            {
                "type": "object",
                "properties": {
                    "min_len": {"type": "integer", "minimum": 0},
                    "column": {"type": "string"},
                },
                "required": ["min_len"],
                "additionalProperties": False,
            },
        ),
        StubOperator(
            _info("stub_band", "filter", input_columns=("score",)),
            op_band,
            {"type": "object", "required": ["min", "max"]},
        ),
        StubOperator(_info("stub_balance", "selector", input_columns=("label",)), op_balance),
        StubOperator(_info("stub_upper", "mapper"), op_upper),
        StubOperator(
            _info("stub_meta_tag", "mapper", output_columns={"note": "metadata"}), op_meta_tag
        ),
        StubOperator(_info("stub_generate", "generator"), op_generate),
        StubOperator(_info("stub_split", "selector"), op_split),
        StubOperator(_info("stub_bad_drop", "filter"), op_bad_drop),
        StubOperator(_info("stub_lose_row", "filter"), op_lose_row),
        StubOperator(_info("stub_wrong_key", "mapper"), op_wrong_key),
        StubOperator(_info("stub_unseeded", "selector"), op_unseeded),
        StubOperator(
            _info(
                "stub_labeler",
                "labeler",
                endpoint_role="classifier",
                binding_kinds=("label_run",),
                output_columns={"label_humor": "metadata"},
            ),
            op_labeler,
        ),
        StubOperator(
            _info(
                "stub_probe_verdict",
                "labeler",
                detector_labeler_kind="probe_verdict",
                output_columns={"label_humor": "metadata"},
            ),
            op_labeler,
        ),
    ]
}


class StubRegistry:
    """Implements services.operator_port.OperatorRegistry over STUBS."""

    def __init__(self, operators: dict[str, StubOperator] | None = None) -> None:
        self.operators = dict(operators or STUBS)
        self.not_allowed: set[str] = set()
        self.pending: list[tuple[StepSpec, str, list[Any]]] = []
        self.dispatched: list[StepSpec] = []
        self.label_store: dict[tuple[str, str], float] = {}
        self.endpoint_calls = 0
        self.executions = 0

    def _get(self, name: str, version: str) -> StubOperator:
        op = self.operators.get(name)
        if op is None or op.info.version != version:
            raise OperatorRefusal("operator_not_found", f"{name} {version} is not installed")
        return op

    def get(self, name: str, version: str) -> OperatorInfo:
        return self._get(name, version).info

    def is_allowed(self, name: str, version: str) -> bool:
        return name not in self.not_allowed

    def current_version(self, name: str) -> str | None:
        op = self.operators.get(name)
        return op.info.version if op else None

    def validate_params(self, name: str, version: str, params: dict[str, Any]) -> list[str]:
        validator = jsonschema.Draft202012Validator(self._get(name, version).schema)
        return [
            f"{'/'.join(map(str, e.path)) or '(params)'}: {e.message}"
            for e in validator.iter_errors(params)
        ]

    def dispatch_step(self, spec: StepSpec, link_task: str, link_args: list[Any]) -> None:
        self.dispatched.append(spec)
        self.pending.append((spec, link_task, link_args))

    # --- the stub executor -------------------------------------------------------------------

    def run_pending(self) -> int:
        ran = 0
        while self.pending:
            spec, _, _ = self.pending.pop(0)
            self.execute(spec)
            ran += 1
        return ran

    def execute(self, spec: StepSpec) -> None:
        self.executions += 1
        op = self._get(spec.operator, spec.version)
        source = resolve_under_data_dir(spec.input_dir)
        tables = [pq.read_table(p) for p in sorted(source.glob("part-*.parquet"))]
        table = pa.concat_tables(tables) if tables else pa.table({})
        rows = table.to_pylist()
        ctx = Context(
            spec.step_seed, dict(spec.column_roles), spec.rowkey_scheme, spec.bindings, self
        )
        outcome = op.run(rows, spec.params, ctx)
        staged = staging_dir() / f"step-{uuid.uuid4().hex}"
        staged.mkdir(parents=True)
        out_schema = table.schema
        for name in outcome.output_roles or {}:
            if name not in out_schema.names and outcome.rows:
                out_schema = out_schema.append(
                    pa.field(name, pa.array([outcome.rows[0][name]]).type)
                )
        pq.write_table(
            pa.Table.from_pylist(outcome.rows, schema=out_schema), staged / "part-00000.parquet"
        )
        pq.write_table(
            pa.Table.from_pylist(outcome.events, schema=EVENT_SCHEMA), staged / EVENTS_FILE
        )
        kinds = Counter(e["kind"] for e in outcome.events)
        changed = kinds["changed"]
        dropped = kinds["dropped"]
        meta = {
            "rows_in": len(rows),
            "rows_kept": len(rows) - changed - dropped,
            "rows_changed": changed,
            "rows_dropped": dropped,
            "rows_added": kinds["added"],
            "rows_split_assigned": kinds["split_assigned"],
            "output_column_roles": outcome.output_roles or dict(spec.column_roles),
            "split_roles": outcome.split_roles,
            "error": None,
        }
        meta.update(outcome.meta_override)
        (staged / META_FILE).write_text(json.dumps(meta))
        destination = resolve_under_data_dir(spec.output_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        staged.rename(destination)


def install_stubs(monkeypatch: Any, registry: StubRegistry | None = None) -> StubRegistry:
    """Install a stub registry for one test (restored by monkeypatch)."""
    from src.services import operator_port

    stub = registry or StubRegistry()
    monkeypatch.setattr(operator_port, "_registry", stub)
    return stub


def body(*steps: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [{"operator": n, "version": "1", "params": p} for n, p in steps],
    }


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
