"""Test-only operators (FTID 003 section 8). Registered ONLY when ``OPERATOR_TEST_FIXTURES`` is set.

``native/registrations.fixture_operators()`` returns these only under that settings flag, so they
never appear in a production registry (``test_registry.py`` builds a production-config registry
and asserts their absence). Several are WRONG ON PURPOSE so the framework has something real to
catch: ``fx_drop_no_reason``, ``fx_lose_row``, ``fx_mutate_silently``, ``fx_filter_changes``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from ..context import RunContext
from ..manifest import ColumnSpec, OperatorManifest, ResourceSpec, ThresholdSpec
from ..protocol import OperatorResult

PROVIDER_VERSION = "fixtures-1"


def _schema(
    properties: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def _manifest(name: str, kind: str, description: str, **extra: Any) -> OperatorManifest:
    values: dict[str, Any] = {
        "name": name,
        "version": "1",
        "provider": "native",
        "provider_version": PROVIDER_VERSION,
        "kind": kind,
        "description": description,
        "params_schema": _schema(),
        "resources": ResourceSpec(queue="curation"),
        "deterministic": True,
    }
    values.update(extra)
    return OperatorManifest(**values)


TEXT_IN = (ColumnSpec(name="text", type="string"),)


def _texts(batch: pa.Table, column: str) -> list[str]:
    return [t or "" for t in batch.column(column).to_pylist()]


class KeepAll:
    manifest = _manifest("fx_keep_all", "filter", "Keeps every row; drops nothing.")

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        return OperatorResult(output=batch)


class DropShort:
    """A threshold filter: drop rows whose text is shorter than ``min_len`` characters."""

    manifest = _manifest(
        "fx_drop_short",
        "filter",
        "Drops rows whose text is shorter than a minimum length in characters.",
        input_columns=TEXT_IN,
        params_schema=_schema(
            {
                "min_len": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 10,
                    "title": "Minimum length",
                    "x-unit": "characters",
                    "x-hint": "Rows shorter than this are dropped.",
                    "x-widget": "slider",
                },
                "column": {"type": "string", "default": "text", "title": "Text column"},
            },
            ["min_len"],
        ),
        thresholds=(
            ThresholdSpec(
                param="min_len", statistic="text_length", unit="characters", drop_when="below"
            ),
        ),
    )

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]:
        column = str(params.get("column", "text"))
        return {"text_length": pa.array([len(t) for t in _texts(batch, column)], pa.float64())}

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        column, minimum = str(params.get("column", "text")), int(params["min_len"])
        lengths = self.compute_statistics(batch, params, ctx)["text_length"].to_pylist()
        keep, events = [], []
        for i, row in enumerate(batch.select(["_dw_row_key", "_dw_occurrence"]).to_pylist()):
            if lengths[i] < minimum:
                events.append(
                    ctx.drop(
                        row,
                        "too_short",
                        f"{column} has {int(lengths[i])} characters, below {minimum}",
                        "text_length",
                        lengths[i],
                        minimum,
                        "<",
                    )
                )
            else:
                keep.append(i)
        return OperatorResult(output=batch.take(pa.array(keep, pa.int64())), events=events)


class DropNoReason:
    manifest = _manifest("fx_drop_no_reason", "filter", "WRONG ON PURPOSE: drops with no reason.")

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        first = batch.slice(0, 1).to_pylist()[0]
        event = ctx.drop(first, "", "", None)  # refused at construction
        return OperatorResult(output=batch.slice(1), events=[event])


class LoseRow:
    manifest = _manifest("fx_lose_row", "filter", "WRONG ON PURPOSE: loses its first row silently.")

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        return OperatorResult(output=batch.slice(1))


class MutateSilently:
    manifest = _manifest(
        "fx_mutate_silently",
        "mapper",
        "WRONG ON PURPOSE: changes text without a changed event.",
        input_columns=TEXT_IN,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        if batch.num_rows == 0:
            return OperatorResult(output=batch)
        index = batch.schema.get_field_index("text")
        changed = pc.utf8_upper(batch.column("text"))
        return OperatorResult(output=batch.set_column(index, "text", changed))


class FilterChanges:
    manifest = _manifest(
        "fx_filter_changes", "filter", "WRONG ON PURPOSE: a filter that changes rows."
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        return MapperUpper().run(batch, params, ctx)


class MapperUpper:
    manifest = _manifest(
        "fx_mapper_upper", "mapper", "Upper-cases the text column.", input_columns=TEXT_IN
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        rows = batch.to_pylist()
        events = []
        for row in rows:
            new_text = (row["text"] or "").upper()
            if new_text == row["text"]:
                continue
            old = (row["_dw_row_key"], row["_dw_occurrence"])
            row["text"] = new_text
            event = ctx.change(
                old, ctx.row_key(row), "upper_cased", "text upper-cased", "length", len(new_text)
            )
            row["_dw_row_key"], row["_dw_occurrence"] = event.new_row_key, event.new_occurrence
            events.append(event)
        return OperatorResult(output=pa.Table.from_pylist(rows, schema=batch.schema), events=events)


class DedupExact:
    """Dataset scope: drop later rows whose text equals an earlier row's; name the kept key."""

    manifest = _manifest(
        "fx_dedup_exact",
        "deduplicator",
        "Drops rows whose text exactly repeats an earlier row's.",
        scope="dataset",
        input_columns=TEXT_IN,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        assert ctx.input_reader is not None
        seen: dict[str, str] = {}
        kept: list[pa.RecordBatch] = []
        events = []
        for record_batch in ctx.input_reader(None):
            table = pa.Table.from_batches([record_batch])
            keep = []
            for i, row in enumerate(
                table.select(["_dw_row_key", "_dw_occurrence", "text"]).to_pylist()
            ):
                text = row["text"] or ""
                if text in seen:
                    events.append(
                        ctx.drop(
                            row,
                            "duplicate",
                            f"duplicate of {seen[text][:12]}",
                            "exact_match",
                            1.0,
                            kept_key=seen[text],
                        )
                    )
                else:
                    seen[text] = row["_dw_row_key"]
                    keep.append(i)
            kept.extend(table.take(pa.array(keep, pa.int64())).to_batches())
            ctx.check_cancel()
        schema = kept[0].schema if kept else batch.schema
        return OperatorResult(output=pa.Table.from_batches(kept, schema=schema), events=events)


class GeneratorEcho:
    manifest = _manifest(
        "fx_generator_echo",
        "generator",
        "Adds one echoed row per input row, recording its parent.",
        input_columns=TEXT_IN,
        deterministic=True,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        new_rows, events = [], []
        for row in batch.to_pylist():
            new = dict(row)
            new["text"] = f"{row['text']} (echo)"
            new["_dw_origin"] = "generated"
            new["_dw_parent_keys"] = [row["_dw_row_key"]]
            key = ctx.row_key(new)
            event = ctx.add(key, [row["_dw_row_key"]], "echo", "echo of its parent")
            new["_dw_row_key"], new["_dw_occurrence"] = key, event.occurrence
            new_rows.append(new)
            events.append(event)
        added = pa.Table.from_pylist(new_rows, schema=batch.schema) if new_rows else None
        return OperatorResult(output=batch, events=events, added=added)


class SplitHalf:
    """A selector with the assign_split effect (FR-003.27): every other row goes to ``test``."""

    manifest = _manifest(
        "fx_split_half", "selector", "Assigns every other row to a held-out split."
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        rows = batch.to_pylist()
        events = []
        for i, row in enumerate(rows):
            split = "test" if i % 2 else "train"
            if row.get("_dw_split") != split:
                events.append(ctx.assign_split(row, split, "split", f"assigned to {split}"))
                row["_dw_split"] = split
        schema = batch.schema
        if "_dw_split" not in schema.names:
            schema = schema.append(pa.field("_dw_split", pa.string()))
        return OperatorResult(
            output=pa.Table.from_pylist(rows, schema=schema),
            events=events,
            report={"split_roles": {"train": {"held_out": False}, "test": {"held_out": True}}},
        )


class Slow:
    """Sleeps per batch, polling cancellation: for preview timeouts and cancel tests."""

    manifest = _manifest(
        "fx_slow",
        "filter",
        "Keeps every row after waiting a number of seconds per batch.",
        params_schema=_schema(
            {"seconds": {"type": "number", "minimum": 0, "maximum": 120, "default": 1}}
        ),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        deadline = time.monotonic() + float(params.get("seconds", 1))
        while time.monotonic() < deadline:
            ctx.check_cancel()
            time.sleep(0.05)
        return OperatorResult(output=batch)


class EndpointProbe:
    """Declares an endpoint role and a lease, for lease-aware step tests (FR-003.18)."""

    manifest = _manifest(
        "fx_endpoint_probe",
        "labeler",
        "Adds the resolved endpoint's model name as a column.",
        resources=ResourceSpec(queue="labeling", endpoint_role="judge", needs_lease=True),
        output_columns=(ColumnSpec(name="judge_model", type="string", role="metadata"),),
        deterministic=False,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        model = ctx.endpoint.model or ""
        out = batch.append_column("judge_model", pa.array([model] * batch.num_rows, pa.string()))
        return OperatorResult(output=out, output_roles={"judge_model": "metadata"})


class RelayEcho:
    """A native generation-stage operator on the loopback relay (the T-32 fallback shape): one
    chat request per row, sent through the relay with the row's key, its reply as a column."""

    manifest = _manifest(
        "fx_relay_echo",
        "labeler",
        "Asks the generation endpoint to echo each row, through the loopback relay.",
        input_columns=TEXT_IN,
        resources=ResourceSpec(queue="labeling", endpoint_role="generation"),
        output_columns=(ColumnSpec(name="reply", type="string", role="metadata"),),
        deterministic=False,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        import httpx

        from ..data_designer.relay import ROW_KEY_HEADER, LoopbackRelay

        replies = []
        with LoopbackRelay(ctx.endpoint, body_overrides=ctx.body_overrides) as relay:
            with httpx.Client(timeout=30) as client:
                for row in batch.select(["_dw_row_key", "text"]).to_pylist():
                    response = client.post(
                        f"{relay.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {relay.nonce}",
                            ROW_KEY_HEADER: row["_dw_row_key"],
                        },
                        json={
                            "model": ctx.endpoint.model or "m",
                            "messages": [{"role": "user", "content": row["text"]}],
                        },
                    )
                    body = response.json()
                    replies.append(
                        body["choices"][0]["message"]["content"]
                        if response.status_code == 200
                        else None
                    )
            ctx.relay_records.extend(relay.records)
        out = batch.append_column("reply", pa.array(replies, pa.string()))
        return OperatorResult(output=out, output_roles={"reply": "metadata"})


FIXTURE_OPERATORS: tuple[type, ...] = (
    KeepAll,
    DropShort,
    DropNoReason,
    LoseRow,
    MutateSilently,
    FilterChanges,
    MapperUpper,
    DedupExact,
    GeneratorEcho,
    SplitHalf,
    Slow,
    EndpointProbe,
    RelayEcho,
)
