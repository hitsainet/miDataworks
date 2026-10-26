"""The Data-Juicer worker: the step and preview tasks (ADR-010; FR-003.14; FTASKS 7.5, 7.6).

Runs ONLY in the ``hitsai/midataworks-datajuicer`` image, as a Celery worker consuming the
``datajuicer`` and ``datajuicer_preview`` queues. It exchanges rows with the backend as Parquet
paths on the shared data volume and imports nothing from ``src``: the standard library, Celery,
pyarrow, ``datasets`` (a Data-Juicer dependency, needed to hand Data-Juicer a dataset) and
Data-Juicer only. ``tests/unit/test_datajuicer_runner_imports.py`` walks this module's abstract
syntax tree and fails on anything else, because this image has neither the backend's dependencies
nor its database credentials. It NEVER touches PostgreSQL.

``midataworks.datajuicer.step`` writes, under ``<output_dir>`` (staged then renamed):

- ``output.parquet`` — the rows Data-Juicer kept (mapped, for a mapper), ``_dw_`` columns carried,
  Data-Juicer's own ``__dj__`` columns removed;
- ``decisions.parquet`` — per input row: ``row_key``, ``occurrence``, ``keep``, ``stats_json``
  (the row's ``__dj__stats__``) and ``kept_key`` (a deduplicator's survivor);

or, when it fails, ``error.json`` (message and traceback) and the task re-raises, so the backend's
finaliser (linked as the error callback too) reports the Data-Juicer traceback (FTASKS 7.6).
The backend's ``midataworks.operators.step.finalize_datajuicer`` turns these into events.

``midataworks.datajuicer.preview`` runs the same branches on a seeded sample and RETURNS the
decisions and the sampled rows (small: at most the preview cap); it writes nothing.

Start (inside the image)::

    celery -A src.operators.datajuicer.runner worker -Q datajuicer,datajuicer_preview \\
        -n datajuicer@%h --concurrency 2
"""

from __future__ import annotations

import json
import os
import random
import shutil
import traceback
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from celery import Celery

QUEUE = "datajuicer"
PREVIEW_QUEUE = "datajuicer_preview"
PING_TASK = "midataworks.datajuicer.ping"
STEP_TASK = "midataworks.datajuicer.step"
PREVIEW_TASK = "midataworks.datajuicer.preview"
STATS = "__dj__stats__"
DECISION_SCHEMA = pa.schema(
    [
        ("row_key", pa.string()),
        ("occurrence", pa.int32()),
        ("keep", pa.bool_()),
        ("stats_json", pa.string()),
        ("kept_key", pa.string()),
    ]
)


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data/dataworks")).resolve()


def _confined(path: str) -> Path:
    """Refuse a path outside the data volume (ADR-004, ADR-015). Relative paths are under it."""
    root = _data_dir()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{candidate} is outside the data directory {root}")
    return candidate


def ping_copy(input_path: str, output_path: str) -> int:
    """Copy every row from one Parquet file to another, staged then renamed. Returns the count."""
    source = _confined(input_path)
    destination = _confined(output_path)
    table = pq.read_table(source)
    staging = _data_dir() / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"dj-{os.getpid()}-{destination.name}"
    try:
        pq.write_table(table, staged)
        with open(staged, "rb") as handle:
            os.fsync(handle.fileno())
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, destination)
    finally:
        if staged.exists():
            staged.unlink()
    return int(table.num_rows)


# --------------------------------------------------------------------------------------------
# Running one operator
# --------------------------------------------------------------------------------------------


def _read_parts(input_dir: str) -> pa.Table:
    directory = _confined(input_dir)
    parts = sorted(directory.glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"{directory} has no part-*.parquet files")
    return pa.concat_tables([pq.read_table(p) for p in parts], promote_options="default")


def _strip_dj(table: pa.Table) -> pa.Table:
    return table.select([n for n in table.schema.names if not n.startswith("__dj__")])


class RuntimeInstallRefused(RuntimeError):
    """Data-Juicer tried to install a package while running (T-10 forbids it)."""

    code = "runtime_install_refused"


def _refuse_install(cls: Any, package_spec: Any, pip_args: Any = None) -> None:
    raise RuntimeInstallRefused(
        f"Data-Juicer tried to install {package_spec!r} at runtime; add it to "
        "datajuicer/requirements.txt (T-10: nothing is installed while the app runs)."
    )


def forbid_runtime_installs() -> None:
    """Make Data-Juicer's lazy loader refuse to install anything (T-10).

    py-data-juicer 1.6.0 pip/uv-installs a missing dependency on first use (spike 1.2 watched it
    install torch, ray and the CUDA libraries) and offers no setting to turn that off, so the one
    method that installs is replaced by a refusal. Every dependency an offered operator imports is
    installed by the image (``datajuicer/requirements.txt`` + ``constraints.txt``); a missing one is
    a build defect that must fail loudly, never a download in production.
    ``datajuicer/tests/test_no_runtime_installs.py`` pins this, and the image build runs it.
    """
    from data_juicer.utils import lazy_loader

    lazy_loader.LazyLoader._install_package = classmethod(_refuse_install)


def apply(
    table: pa.Table, op_name: str, kind: str, params: dict[str, Any], num_proc: int | None
) -> tuple[pa.Table, pa.Table]:
    """Run one Data-Juicer operator over ``table``. Returns (output rows, per-row decisions)."""
    forbid_runtime_installs()
    from data_juicer.ops.load import load_ops
    from datasets import Dataset

    args = dict(params)
    if num_proc:
        args["num_proc"] = num_proc
    op = load_ops([{op_name: args}])[0]
    keys = table.column("_dw_row_key").to_pylist()
    occurrences = table.column("_dw_occurrence").to_pylist()
    ds = Dataset(table)
    keep: list[bool]
    stats: list[Any] = [None] * table.num_rows
    kept_key: list[str | None] = [None] * table.num_rows
    if kind == "filter":
        if STATS not in ds.column_names:
            ds = ds.add_column(name=STATS, column=[{}] * ds.num_rows)
        ds = op.run(ds, reduce=False)
        stats = list(ds[STATS])
        batch = ds.to_dict()
        if op.is_batched_op():
            keep = [bool(k) for k in op.process_batched(batch)]
        else:
            keep = [bool(op.process_single(row)) for row in ds]
        output = ds.data.table.filter(pa.array(keep))
    elif kind == "deduplicator":
        from data_juicer.utils.constant import HashKeys

        hashed = ds.map(op.compute_hash, batched=op.is_batched_op())
        first: dict[Any, str] = {}
        keep = []
        for index, value in enumerate(hashed[HashKeys.hash]):
            if value in first:
                keep.append(False)
                kept_key[index] = first[value]
            else:
                first[value] = keys[index]
                keep.append(True)
        output = table.filter(pa.array(keep))
    elif kind == "mapper":
        output = op.run(ds).data.table
        keep = [True] * table.num_rows
    elif kind == "selector":
        output = op.process(ds).data.table
        survivors = set(
            zip(
                output.column("_dw_row_key").to_pylist(),
                output.column("_dw_occurrence").to_pylist(),
                strict=True,
            )
        )
        keep = [(k, o) in survivors for k, o in zip(keys, occurrences, strict=True)]
    else:
        raise ValueError(f"unsupported Data-Juicer kind {kind!r}")
    decisions = pa.Table.from_pylist(
        [
            {
                "row_key": k,
                "occurrence": int(o),
                "keep": bool(kp),
                "stats_json": json.dumps(s, sort_keys=True, default=str) if s is not None else None,
                "kept_key": kk,
            }
            for k, o, kp, s, kk in zip(keys, occurrences, keep, stats, kept_key, strict=True)
        ],
        schema=DECISION_SCHEMA,
    )
    return _strip_dj(output), decisions


class ParamsInvalid(ValueError):
    """The worker-side re-validation refused the parameters (FR-003.7): before ``load_ops``."""

    code = "params_invalid"


def validate_params(payload: dict[str, Any]) -> None:
    """Re-validate ``params`` against the manifest's schema the backend sent, before the engine
    sees them. ``jsonschema`` is pinned in this image (datajuicer/requirements.txt)."""
    import jsonschema

    schema = payload.get("params_schema")
    if not isinstance(schema, dict):
        raise ParamsInvalid("the step carries no params_schema to validate against")
    errors = sorted(
        "/" + "/".join(str(p) for p in e.absolute_path) + ": " + e.message
        for e in jsonschema.Draft202012Validator(schema).iter_errors(payload["params"])
    )
    if errors:
        raise ParamsInvalid("; ".join(errors))


def run_step(payload: dict[str, Any]) -> dict[str, Any]:
    """One step: stage, then rename into ``output_dir``; on failure, ``error.json`` and re-raise."""
    destination = _confined(payload["output_dir"])
    staging = _data_dir() / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"dj-{uuid.uuid4().hex}"
    staged.mkdir()
    try:
        validate_params(payload)
        table = _read_parts(payload["input_dir"])
        output, decisions = apply(
            table, payload["op_name"], payload["kind"], payload["params"], payload.get("num_proc")
        )
        pq.write_table(output, staged / "output.parquet")
        pq.write_table(decisions, staged / "decisions.parquet")
        rows = table.num_rows
    except Exception as exc:
        (staged / "error.json").write_text(
            json.dumps(
                {
                    "code": getattr(exc, "code", "datajuicer_failed"),
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
        )
        _publish(staged, destination)
        raise
    _publish(staged, destination)
    return {"rows": rows, "output_dir": payload["output_dir"]}


def _publish(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staged, destination)


def run_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """A seeded sample through the operator; returns rows and decisions, writes nothing."""
    validate_params(payload)
    tables = [pq.read_table(_confined(p)) for p in payload["files"]]
    table = pa.concat_tables(tables, promote_options="default") if tables else pa.table({})
    size = min(int(payload["sample_size"]), table.num_rows)
    rng = random.Random(int(payload["seed"]))  # noqa: S311 - a reproducible sample, not a secret
    chosen = sorted(rng.sample(range(table.num_rows), size))
    sample = table.take(pa.array(chosen, pa.int64())) if size else table.slice(0, 0)
    if sample.num_rows == 0:
        return {"rows": [], "output": [], "decisions": []}
    output, decisions = apply(
        sample, payload["op_name"], payload["kind"], payload["params"], payload.get("num_proc")
    )
    return {
        "rows": sample.to_pylist(),
        "output": output.to_pylist(),
        "decisions": decisions.to_pylist(),
    }


app = Celery(
    "midataworks-datajuicer",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
app.conf.update(
    task_default_queue=QUEUE,
    task_routes={
        PREVIEW_TASK: {"queue": PREVIEW_QUEUE},
        "midataworks.datajuicer.*": {"queue": QUEUE},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
)


# shared=False: a shared task would also register in the BACKEND app whenever this module is
# imported there, and the backend would then believe it can run Data-Juicer work itself.
@app.task(name=PING_TASK, shared=False)
def ping(input_path: str, output_path: str) -> dict[str, object]:
    """Round-trip rows through the Data-Juicer container (task 10.2)."""
    return {"rows": ping_copy(input_path, output_path), "output_path": output_path}


@app.task(name=STEP_TASK, shared=False)
def step(payload: dict[str, Any]) -> dict[str, Any]:
    return run_step(payload)


@app.task(name=PREVIEW_TASK, shared=False)
def preview(payload: dict[str, Any]) -> dict[str, Any]:
    return run_preview(payload)
