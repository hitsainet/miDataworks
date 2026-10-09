"""Feature 007's native operators (FTDD 007 sections 4.3 and 6.5; FR-007.2, 007.3, 007.11, 007.12).

- ``native_chat_generate@1`` (labeler, the generation endpoint): the relay path's model call. Each
  input row carries one chat request; it goes through 003's loopback relay with the row key in
  ``X-Dataworks-Row-Key`` and the stage's ``body_overrides``. It runs ONLY as a generation stage
  (``execute_in_process`` with a ``GenerationStageSpec``): a recipe step or a preview is refused,
  because a build must never call a model (FR-002.2).
- ``dw_generated_rows@1`` (generator, dataset scope): adds the rows a COMPLETED, BOUND generation
  run recorded. It reads only the chunks listed in the run's ``committed.json`` (written from the
  database's commit markers when the run completed), verifies each file's SHA-256, and refuses a
  run the build did not bind. Each row: ``_dw_origin = generated``, ``_dw_parent_keys`` = prompt
  and seed keys, ``_dw_split`` = the seed row's split (a held-out split is refused), and the
  metadata columns ``generation_run_id``, ``generation_record_index``, ``generation_side``,
  ``generation_model_id`` (carried, not hashed into the key).
- ``dw_judge_filter@1``, ``dw_pair_filter@1`` (selectors) and ``dw_pair_from_scores@1``
  (generator) read a completed, bound label run's published ``labels.parquet`` and touch ONLY
  generated rows; source rows pass with no event. Every drop names its reason and statistic.

None of these opens a database session (003 ``RunContext``).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ...core.storage import resolve_under_data_dir
from ..context import RunContext
from ..errors import OperatorError
from ..manifest import ColumnSpec, OperatorManifest, ResourceSpec
from ..protocol import OperatorResult

PROVIDER_VERSION = "007-1"
REQUEST_COLUMN = "request"
COMMITTED_FILE = "committed.json"

#: Columns each target type's generated rows fill (FTDD 007 section 4.3).
TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "sft": ("prompt", "completion"),
    "kto": ("prompt", "completion"),
    "grpo_prompt": ("prompt",),
    "dpo_steered": ("prompt", "chosen", "rejected"),
    "dpo": ("prompt", "completion"),
}
#: A minimal-pair run's rows (009 FR-009.60): the counterpart text in the run's prompt column.
MINIMAL_PAIR = "minimal_pair"
METADATA_COLUMNS: tuple[tuple[str, pa.DataType], ...] = (
    ("generation_run_id", pa.string()),
    ("generation_record_index", pa.int64()),
    ("generation_side", pa.string()),
    ("generation_model_id", pa.string()),
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _manifest(
    name: str,
    kind: str,
    description: str,
    params: dict[str, Any],
    required: list[str],
    *,
    scope: str = "row",
    output_columns: tuple[ColumnSpec, ...] = (),
    resources: ResourceSpec | None = None,
    binding_kinds: tuple[str, ...] = (),
    deterministic: bool = True,
) -> OperatorManifest:
    return OperatorManifest(
        name=name,
        version="1",
        provider="native",
        provider_version=PROVIDER_VERSION,
        kind=kind,
        scope=scope,
        description=description,
        output_columns=output_columns,
        params_schema=_schema(params, required),
        resources=resources or ResourceSpec(queue="curation"),
        deterministic=deterministic,
        binding_kinds=binding_kinds,
    )


def _require_bound(ctx: RunContext, kind: str, run_id: str) -> None:
    bound = {str(b.get("id")) for b in ctx.bindings if b.get("kind") == kind}
    if run_id not in bound:
        raise OperatorError(
            f"{kind}_not_bound",
            f"{ctx.manifest.ref_text} reads {kind} {run_id}, which this build does not bind. Add "
            f"it to the build's bindings ({{kind: {kind!r}, id: {run_id!r}}}).",
            {"kind": kind, "id": run_id},
        )


def _read_all(ctx: RunContext, batch: pa.Table) -> pa.Table:
    if ctx.input_reader is None:
        return batch
    batches = list(ctx.input_reader(None))
    if not batches:
        return batch
    return pa.Table.from_batches(batches)


def _is_generated(row: Mapping[str, Any]) -> bool:
    return row.get("_dw_origin") == "generated"


# --- the relay path's model call ------------------------------------------------------------


class NativeChatGenerate:
    manifest = _manifest(
        "native_chat_generate",
        "labeler",
        "Sends each row's chat request to the generation endpoint through the loopback relay "
        "(feature 007's generation stages only; never a recipe step).",
        {},
        [],
        output_columns=(
            ColumnSpec(name="text", type="string", role="metadata", required=False),
            ColumnSpec(name="finish_reason", type="string", role="metadata", required=False),
            ColumnSpec(name="model", type="string", role="metadata", required=False),
            ColumnSpec(name="status", type="int64", role="metadata", required=False),
            ColumnSpec(name="error_code", type="string", role="metadata", required=False),
            ColumnSpec(name="error", type="string", role="metadata", required=False),
        ),
        resources=ResourceSpec(queue="labeling", endpoint_role="generation"),
        deterministic=False,
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        import httpx

        from ..data_designer.relay import ROW_KEY_HEADER, LoopbackRelay

        if ctx.step_execution_id is not None or ctx.sample:
            raise OperatorError(
                "generation_stage_only",
                "native_chat_generate runs only as a feature 007 generation stage: a version "
                "build never calls a model (FR-002.2). Bind a generation run instead.",
            )
        lease = _LeaseRef(ctx.lease_id) if ctx.lease_id else None
        rows = batch.select(["_dw_row_key", REQUEST_COLUMN]).to_pylist()
        texts: list[str | None] = []
        finishes: list[str | None] = []
        models: list[str | None] = []
        statuses: list[int] = []
        codes: list[str | None] = []
        errors: list[str | None] = []
        with LoopbackRelay(ctx.endpoint, body_overrides=ctx.body_overrides, lease=lease) as relay:
            with httpx.Client(timeout=600.0) as client:  # the relay owns retries and waits
                for row in rows:
                    ctx.check_cancel()
                    payload = json.loads(row[REQUEST_COLUMN])
                    response = client.post(
                        f"{relay.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {relay.nonce}",
                            ROW_KEY_HEADER: row["_dw_row_key"],
                        },
                        json=payload["body"],
                    )
                    statuses.append(response.status_code)
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    if response.status_code == 200 and isinstance(body, dict):
                        choice = (body.get("choices") or [{}])[0]
                        message = choice.get("message") or {}
                        content = message.get("content")
                        texts.append(None if content is None else str(content))
                        finishes.append(choice.get("finish_reason"))
                        models.append(body.get("model"))
                        codes.append(None)
                        errors.append(None)
                        continue
                    err = body.get("error") if isinstance(body, dict) else None
                    texts.append(None)
                    finishes.append(None)
                    models.append(None)
                    codes.append(
                        str(err.get("code")).lower()
                        if isinstance(err, dict) and err.get("code")
                        else None
                    )
                    errors.append(str(err.get("message"))[:500] if isinstance(err, dict) else None)
            ctx.relay_records.extend(relay.records)
        out = batch
        for name, values, typ in (
            ("text", texts, pa.string()),
            ("finish_reason", finishes, pa.string()),
            ("model", models, pa.string()),
            ("status", statuses, pa.int64()),
            ("error_code", codes, pa.string()),
            ("error", errors, pa.string()),
        ):
            out = out.append_column(name, pa.array(values, typ))
        return OperatorResult(
            output=out,
            output_roles=dict.fromkeys(("text", "finish_reason", "model", "status"), "metadata")
            | {"error_code": "metadata", "error": "metadata"},
        )


class _LeaseRef:
    def __init__(self, lease_id: str) -> None:
        self.lease_id = lease_id

    def __repr__(self) -> str:  # never print the lease id
        return "_LeaseRef(***)"


# --- generated rows into versions -----------------------------------------------------------


def committed_manifest(run_id: str) -> dict[str, Any]:
    path = resolve_under_data_dir("runs", run_id, "gen", COMMITTED_FILE)
    if not path.is_file():
        raise OperatorError(
            "generation_run_not_published",
            f"Generation run {run_id} has no committed record list; bind a completed run.",
            {"generation_run_id": run_id},
        )
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("run_id") != run_id or data.get("state") != "completed":
        raise OperatorError(
            "generation_run_not_published",
            f"Generation run {run_id}'s record list does not describe a completed run.",
            {"generation_run_id": run_id},
        )
    return data


def _verified(entry: Mapping[str, Any]) -> pa.Table:
    from ...services.identity import file_sha256

    path = resolve_under_data_dir(str(entry["path"]))
    if not path.is_file() or file_sha256(path) != entry["sha256"]:
        raise OperatorError(
            "generation_chunk_changed",
            f"A committed generation chunk ({entry['path']}) is missing or no longer matches its "
            "recorded SHA-256; nothing is read from it.",
            {"path": entry["path"]},
        )
    return pq.read_table(path)


def committed_records(manifest: Mapping[str, Any], stage: str) -> list[dict[str, Any]]:
    """Every record of ``stage`` in the COMMITTED chunks the manifest lists, in index order."""
    rows: list[dict[str, Any]] = []
    for entry in manifest.get("chunks") or []:
        if entry.get("stage") != stage:
            continue
        rows.extend(_verified(entry).to_pylist())
    rows.sort(key=lambda r: (int(r["record_index"]), str(r.get("side") or "")))
    return rows


def seed_splits(manifest: Mapping[str, Any]) -> dict[str, str]:
    entry = manifest.get("seeds")
    if not entry:
        return {}
    table = _verified(entry)
    keys = table.column("row_key").to_pylist()
    splits = table.column("split").to_pylist()
    return {str(k): str(s) for k, s in zip(keys, splits, strict=True)}


class GeneratedRows:
    manifest = _manifest(
        "dw_generated_rows",
        "generator",
        "Adds the rows a completed generation run recorded, with their lineage; never calls a "
        "model.",
        {
            "generation_run_id": {"type": "string", "minLength": 1, "title": "Generation run"},
            "target_type": {
                "type": "string",
                "enum": ["sft", "kto", "grpo_prompt", "dpo", "detector"],
                "title": "Target type",
            },
        },
        ["generation_run_id", "target_type"],
        scope="dataset",
        output_columns=tuple(
            ColumnSpec(name=n, type=str(t), role="metadata", required=False)
            for n, t in METADATA_COLUMNS
        ),
        binding_kinds=("generation_run",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["generation_run_id"])
        target = str(params["target_type"])
        _require_bound(ctx, "generation_run", run_id)
        manifest = committed_manifest(run_id)
        held_out = set(manifest.get("held_out_splits") or [])
        steered = manifest.get("mode") == "steered_pairs"
        minimal = manifest.get("mode") == "minimal_pairs"
        shape = "dpo_steered" if steered else MINIMAL_PAIR if minimal else target
        if manifest.get("target_type") != target:
            raise OperatorError(
                "target_type_mismatch",
                f"Generation run {run_id} produced {manifest.get('target_type')} rows, not "
                f"{target}.",
            )
        table = _read_all(ctx, batch)
        schema = table.schema
        fills = (str(manifest.get("prompt_column") or ""),) if minimal else TARGET_COLUMNS[shape]
        missing = [c for c in fills if c not in schema.names]
        if missing:
            raise OperatorError(
                "target_columns_missing",
                f"The version has no column(s) {missing}; generated {shape} rows need them.",
                {"missing": missing},
            )
        splits = seed_splits(manifest)
        rows = (
            self._minimal_rows(manifest, fills[0])
            if minimal
            else self._rows(manifest, shape, steered)
        )
        new_rows: list[dict[str, Any]] = []
        events = []
        for row in rows:
            split = splits.get(str(row["seed_row_key"]))
            if split is None:
                raise OperatorError(
                    "seed_row_unknown",
                    f"Generated row {row['record_index']} names a seed row the run did not record.",
                )
            if split in held_out:
                raise OperatorError(
                    "generated_into_held_out",
                    f"Generated rows may not enter the held-out split {split!r} (FR-007.36).",
                    {"split": split},
                )
            new: dict[str, Any] = dict.fromkeys(schema.names)
            new.update(row["values"])
            parents = list(dict.fromkeys([row["prompt_row_key"], row["seed_row_key"]]))
            new["_dw_origin"] = "generated"
            new["_dw_parent_keys"] = parents
            new["_dw_split"] = split
            new["_dw_source_id"] = None
            new["_dw_source_locator"] = None
            new["generation_run_id"] = run_id
            new["generation_record_index"] = int(row["record_index"])
            new["generation_side"] = row["side"]
            new["generation_model_id"] = row["model_id"]
            key = ctx.row_key(new)
            event = ctx.add(key, parents, "generated", f"generated by run {run_id}")
            new["_dw_row_key"], new["_dw_occurrence"] = key, event.occurrence
            new_rows.append(new)
            events.append(event)
        fields = list(schema) + [
            pa.field(n, t) for n, t in METADATA_COLUMNS if n not in schema.names
        ]
        added = pa.Table.from_pylist(new_rows, schema=pa.schema(fields)) if new_rows else None
        return OperatorResult(
            output=table,
            events=events,
            added=added,
            report={"generation_run_id": run_id, "rows_added": len(new_rows), "shape": shape},
        )

    @staticmethod
    def _minimal_rows(manifest: Mapping[str, Any], column: str) -> list[dict[str, Any]]:
        """Each generated counterpart, written into the seed's text column (009 FR-009.60). Its
        lineage names the seed, so the minimal-pair operators can pair the two."""
        return [
            {
                "record_index": r["record_index"],
                "seed_row_key": r["seed_row_key"],
                "prompt_row_key": r["prompt_row_key"],
                "side": None,
                "model_id": r["model_id"],
                "values": {column: r["text"]},
            }
            for r in committed_records(manifest, "respond")
            if r["outcome"] == "generated"
        ]

    @staticmethod
    def _rows(manifest: Mapping[str, Any], shape: str, steered: bool) -> list[dict[str, Any]]:
        if shape == "grpo_prompt":
            out = []
            for r in committed_records(manifest, "expand"):
                if r["outcome"] != "generated":
                    continue
                out.append(
                    {
                        "record_index": r["record_index"],
                        "seed_row_key": r["seed_row_key"],
                        "prompt_row_key": r["prompt_row_key"],
                        "side": None,
                        "model_id": r["model_id"],
                        "values": {"prompt": r["text"]},
                    }
                )
            return out
        records = committed_records(manifest, "respond")
        if not steered:
            return [
                {
                    "record_index": r["record_index"],
                    "seed_row_key": r["seed_row_key"],
                    "prompt_row_key": r["prompt_row_key"],
                    "side": None,
                    "model_id": r["model_id"],
                    "values": {"prompt": r["prompt"], "completion": r["text"]},
                }
                for r in records
                if r["outcome"] == "generated"
            ]
        chosen = str(manifest.get("chosen_side"))
        by_prompt: dict[tuple[str, int], dict[str, dict[str, Any]]] = defaultdict(dict)
        for r in records:
            by_prompt[(r["prompt_row_key"], int(r["response_index"]))][str(r["side"])] = r
        out = []
        for (_, _), sides in sorted(by_prompt.items(), key=lambda kv: kv[0]):
            a, b = sides.get("a"), sides.get("b")
            if a is None or b is None:
                continue
            if not all(
                s["outcome"] == "generated" and s["steering_check"] == "match" for s in (a, b)
            ):
                continue
            win, lose = (a, b) if chosen == "a" else (b, a)
            out.append(
                {
                    "record_index": win["record_index"],
                    "seed_row_key": win["seed_row_key"],
                    "prompt_row_key": win["prompt_row_key"],
                    "side": chosen,
                    "model_id": win["model_id"],
                    "values": {
                        "prompt": win["prompt"],
                        "chosen": win["text"],
                        "rejected": lose["text"],
                    },
                }
            )
        out.sort(key=lambda r: int(r["record_index"]))
        return out


# --- filters over a label run ---------------------------------------------------------------


def _labels(label_run_id: str) -> dict[str, dict[str, Any]]:
    path = resolve_under_data_dir("runs", label_run_id, "labels.parquet")
    if not path.is_file():
        raise OperatorError(
            "label_run_not_published",
            f"Label run {label_run_id} has no published labels; bind a completed run.",
            {"label_run_id": label_run_id},
        )
    table = pq.read_table(path, columns=["row_key", "outcome", "parsed_value", "probability"])
    out: dict[str, dict[str, Any]] = {}
    for row in table.to_pylist():
        parsed = json.loads(row["parsed_value"]) if row["parsed_value"] else {}
        out[str(row["row_key"])] = {
            "outcome": row["outcome"],
            "parsed": parsed if isinstance(parsed, dict) else {},
            "probability": row["probability"],
        }
    return out


def _score(label: Mapping[str, Any]) -> float | None:
    score = label["parsed"].get("score")
    if isinstance(score, int | float) and not isinstance(score, bool):
        return float(score)
    probability = label.get("probability")
    return float(probability) if probability is not None else None


LABEL_RUN = {"type": "string", "minLength": 1, "title": "Label run"}


class JudgeFilter:
    manifest = _manifest(
        "dw_judge_filter",
        "selector",
        "Drops generated rows a judge run rejected (by verdict or score); source rows pass.",
        {
            "label_run_id": LABEL_RUN,
            "keep_outcomes": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Verdicts to keep",
            },
            "min_score": {"type": "number", "title": "Lowest score kept"},
            "only_rows_with": {
                "type": "string",
                "minLength": 1,
                "title": "Only generated rows with this column set",
            },
        },
        ["label_run_id"],
        scope="dataset",
        binding_kinds=("label_run",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["label_run_id"])
        _require_bound(ctx, "label_run", run_id)
        keep = params.get("keep_outcomes")
        min_score = params.get("min_score")
        if keep is None and min_score is None:
            raise OperatorError(
                "params_invalid", "Give the verdicts to keep, a lowest score, or both."
            )
        only = params.get("only_rows_with")
        replaced = keep is not None and len(keep) == 0
        labels = {} if replaced else _labels(run_id)
        table = _read_all(ctx, batch)
        rows = table.to_pylist()
        keep_idx: list[int] = []
        events = []
        for i, row in enumerate(rows):
            if not _is_generated(row) or (only and row.get(only) is None):
                keep_idx.append(i)
                continue
            pair = (row["_dw_row_key"], row["_dw_occurrence"])
            if replaced:
                events.append(
                    ctx.drop(
                        pair,
                        "replaced_by_pair",
                        f"a {only or 'generated'} row replaced by the pairs built from it",
                        "verdict",
                        text="replaced",
                    )
                )
                continue
            label = labels.get(str(row["_dw_row_key"]))
            if label is None:
                events.append(
                    ctx.drop(
                        pair,
                        "judge_missing",
                        "the judge run has no label for this row",
                        "verdict",
                        text="missing",
                    )
                )
                continue
            outcome = str(label["outcome"])
            if keep is not None and outcome not in set(keep):
                events.append(
                    ctx.drop(
                        pair,
                        "judge_rejected",
                        f"verdict {outcome!r} is not one of {sorted(keep)}",
                        "verdict",
                        text=outcome,
                    )
                )
                continue
            if min_score is not None:
                score = _score(label)
                if score is None or score < float(min_score):
                    events.append(
                        ctx.drop(
                            pair,
                            "judge_rejected",
                            f"score {score} is below {min_score}",
                            "score",
                            score,
                            min_score,
                            "below",
                        )
                    )
                    continue
            keep_idx.append(i)
        return OperatorResult(output=table.take(pa.array(keep_idx, pa.int64())), events=events)


class PairFilter:
    manifest = _manifest(
        "dw_pair_filter",
        "selector",
        "Keeps a generated preference pair only when a pairwise judge agrees with its declared "
        "chosen side in both orders; ties and position-inconsistent verdicts are dropped.",
        {
            "label_run_id": LABEL_RUN,
            "rule": {
                "type": "string",
                "enum": ["agree_with_declared"],
                "default": "agree_with_declared",
            },
            "chosen_verdict": {"type": "string", "minLength": 1, "default": "A"},
            "tie_verdicts": {"type": "array", "items": {"type": "string"}, "default": ["tie"]},
        },
        ["label_run_id"],
        scope="dataset",
        binding_kinds=("label_run",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["label_run_id"])
        _require_bound(ctx, "label_run", run_id)
        chosen = str(params.get("chosen_verdict") or "A")
        ties = set(params.get("tie_verdicts") or ["tie"])
        labels = _labels(run_id)
        table = _read_all(ctx, batch)
        rows = table.to_pylist()
        keep_idx: list[int] = []
        events = []
        for i, row in enumerate(rows):
            if not _is_generated(row) or row.get("chosen") is None:
                keep_idx.append(i)
                continue
            pair = (row["_dw_row_key"], row["_dw_occurrence"])
            label = labels.get(str(row["_dw_row_key"]))
            outcome = None if label is None else str(label["outcome"])
            if outcome == chosen:
                keep_idx.append(i)
                continue
            if outcome is None or outcome in ("parse_failure", "skipped"):
                code, reason = "pair_judge_missing", "no usable pairwise verdict for this pair"
            elif outcome == "position_inconsistent":
                code, reason = (
                    "pair_position_inconsistent",
                    "the judge's verdict changed when the two responses swapped places",
                )
            elif outcome in ties:
                code, reason = "pair_tie", f"the judge called it a tie ({outcome})"
            else:
                code, reason = (
                    "pair_judge_disagrees",
                    f"the judge preferred {outcome!r}, not the declared chosen side {chosen!r}",
                )
            events.append(ctx.drop(pair, code, reason, "verdict", text=str(outcome)))
        return OperatorResult(output=table.take(pa.array(keep_idx, pa.int64())), events=events)


class PairFromScores:
    manifest = _manifest(
        "dw_pair_from_scores",
        "generator",
        "Builds one prompt/chosen/rejected row per prompt from the best- and worst-scored "
        "generated responses, when their scores differ by at least the margin.",
        {
            "label_run_id": LABEL_RUN,
            "min_margin": {"type": "number", "minimum": 0, "title": "Smallest score margin"},
        },
        ["label_run_id", "min_margin"],
        scope="dataset",
        binding_kinds=("label_run",),
    )

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        run_id = str(params["label_run_id"])
        _require_bound(ctx, "label_run", run_id)
        margin = float(params["min_margin"])
        labels = _labels(run_id)
        table = _read_all(ctx, batch)
        schema = table.schema
        missing = [
            c for c in ("prompt", "chosen", "rejected", "completion") if c not in schema.names
        ]
        if missing:
            raise OperatorError(
                "target_columns_missing",
                f"The version has no column(s) {missing}; pairs from scores need them.",
                {"missing": missing},
            )
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in table.to_pylist():
            if _is_generated(row) and row.get("completion") is not None:
                groups[str(row["prompt"])].append(row)
        new_rows, events = [], []
        too_small = unscored = 0
        for prompt in sorted(groups):
            scored = []
            for row in groups[prompt]:
                label = labels.get(str(row["_dw_row_key"]))
                score = _score(label) if label is not None else None
                if score is not None:
                    scored.append((score, str(row["_dw_row_key"]), row))
            if len(scored) < 2:
                unscored += 1
                continue
            scored.sort(key=lambda t: (t[0], t[1]))
            worst, best = scored[0], scored[-1]
            if best[0] - worst[0] < margin:
                too_small += 1
                continue
            new: dict[str, Any] = dict.fromkeys(schema.names)
            template = best[2]
            for name in schema.names:
                if not name.startswith("_dw_"):
                    new[name] = template.get(name)
            new["completion"] = None
            new["chosen"], new["rejected"] = best[2]["completion"], worst[2]["completion"]
            parents = [best[1], worst[1]]
            new["_dw_origin"] = "generated"
            new["_dw_parent_keys"] = parents
            new["_dw_split"] = template.get("_dw_split")
            key = ctx.row_key(new)
            event = ctx.add(key, parents, "pair_from_scores", f"scores {best[0]:g} vs {worst[0]:g}")
            new["_dw_row_key"], new["_dw_occurrence"] = key, event.occurrence
            new_rows.append(new)
            events.append(event)
        added = pa.Table.from_pylist(new_rows, schema=schema) if new_rows else None
        return OperatorResult(
            output=table,
            events=events,
            added=added,
            report={
                "pairs_added": len(new_rows),
                "pair_margin_too_small": too_small,
                "prompts_unscored": unscored,
                "min_margin": margin,
            },
        )


OPERATORS: tuple[type, ...] = (
    NativeChatGenerate,
    GeneratedRows,
    JudgeFilter,
    PairFilter,
    PairFromScores,
)
