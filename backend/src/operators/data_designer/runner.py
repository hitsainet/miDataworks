"""The Data Designer worker: step and preview tasks (FR-003.15; PADR ADR-010 amendment 2026-10-07).

Runs ONLY in the ``hitsai/midataworks-designer`` image, as a Celery worker consuming the
``designer`` queue — the Data-Juicer pattern exactly. ``data-designer==0.9.4`` needs
``huggingface-hub<2`` and the backend pins 2.1.1, so the backend never imports ``data_designer``;
it sends work here by task name (``tests/unit/test_designer_runner_imports.py`` keeps this module
to the standard library, Celery, pyarrow, redis, cryptography, jsonschema, Data Designer and the
self-contained relay beside it, and keeps ``data_designer`` out of every backend module).

No database credentials. The model endpoint's URL and model arrive in the task payload; its API
key does NOT (a Celery message sits in Redis): the backend seals the key with the shared
``DESIGNER_HANDOFF_KEY`` and stores it in Redis under a one-step reference with a lifetime; this
worker takes it with ``GETDEL`` and holds it in memory only. Every model call goes through the
loopback relay (``relay.py``), whose nonce is the only thing Data Designer's provider reads from the
environment, for the length of the call.

``midataworks.designer.step`` writes under ``<output_dir>`` (staged then renamed):
``output.parquet`` (``_dw_row_key``, ``_dw_occurrence`` and the output column, one row per record
Data Designer produced) and ``records.json`` (the relay's per-request records); or ``error.json``
and the task re-raises. The backend's ``midataworks.operators.step.finalize_designer`` turns that
into events. ``midataworks.designer.preview`` returns the same for a seeded sample and writes
nothing.

Start (inside the image)::

    celery -A src.operators.data_designer.runner worker -Q designer -n designer@%h --concurrency 2
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import shutil
import tempfile
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from celery import Celery

from .relay import LoopbackRelay

QUEUE = "designer"
PING_TASK = "midataworks.designer.ping"
STEP_TASK = "midataworks.designer.step"
PREVIEW_TASK = "midataworks.designer.preview"
NONCE_ENV = "DW_RELAY_NONCE"
KEY_PREFIX = "dw:designer:key:"
INFERENCE = ("temperature", "top_p", "max_tokens")


# --------------------------------------------------------------------------------------------
# The key hand-off (byte-compatible with backend operators/data_designer/handoff.py)
# --------------------------------------------------------------------------------------------


def _aead(secret: str) -> Any:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(hashlib.sha256(secret.encode("utf-8")).digest())


def open_sealed(ref: str, sealed: bytes, secret: str) -> str:
    blob = base64.b64decode(sealed)
    plain: bytes = _aead(secret).decrypt(blob[:12], blob[12:], ref.encode())
    return plain.decode("utf-8")


def take_key(ref: str | None) -> str | None:
    """Read and delete the step's sealed endpoint key (``GETDEL``); None when the step has none."""
    if ref is None:
        return None
    import redis

    secret = os.environ.get("DESIGNER_HANDOFF_KEY", "")
    if not secret:
        raise RuntimeError("DESIGNER_HANDOFF_KEY is not set in the designer worker")
    client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    sealed = client.getdel(KEY_PREFIX + ref)
    if sealed is None:
        raise RuntimeError(f"the endpoint key for {ref} was already taken or has expired")
    return open_sealed(ref, sealed if isinstance(sealed, bytes) else sealed.encode(), secret)


# --------------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------------


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data/dataworks")).resolve()


def _confined(path: str) -> Path:
    root = _data_dir()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"{candidate} is outside the data directory {root}")
    return candidate


def _read_parts(input_dir: str) -> pa.Table:
    parts = sorted(_confined(input_dir).glob("part-*.parquet"))
    if not parts:
        raise FileNotFoundError(f"{input_dir} has no part-*.parquet files")
    return pa.concat_tables([pq.read_table(p) for p in parts], promote_options="default")


def _publish(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(staged, destination)


# --------------------------------------------------------------------------------------------
# Running one column
# --------------------------------------------------------------------------------------------


@dataclass
class RelayTarget:
    base_url: str
    model: str | None
    api_key: str | None = field(repr=False)
    is_millm: bool


@dataclass(frozen=True)
class LeaseRef:
    lease_id: str


def validate_params(payload: dict[str, Any]) -> None:
    import jsonschema

    errors = sorted(
        "/" + "/".join(str(p) for p in e.absolute_path) + ": " + e.message
        for e in jsonschema.Draft202012Validator(payload["params_schema"]).iter_errors(
            payload["params"]
        )
    )
    if errors:
        raise ValueError("params_invalid: " + "; ".join(errors))


def create_records(
    table: pa.Table, payload: dict[str, Any], base_url: str | None, artifact_dir: str
) -> list[dict[str, Any]]:  # pragma: no cover - exercised in designer/tests with the real library
    """Data Designer over ``table`` (ordered seed sampling, one column, ``len(table)`` records)."""
    import data_designer.config as c
    from data_designer.interface import DataDesigner

    params = payload["params"]
    providers = []
    models = []
    if base_url is not None:
        providers.append(
            c.ModelProvider(
                name="dw-relay", endpoint=base_url, provider_type="openai", api_key=NONCE_ENV
            )
        )
        inference = {k: params[k] for k in INFERENCE if k in params}
        models.append(
            c.ModelConfig(
                alias="dw",
                model=payload["endpoint"].get("model") or "default",
                provider="dw-relay",
                skip_health_check=True,
                inference_parameters=c.ChatCompletionInferenceParams(
                    max_parallel_requests=1, **inference
                ),
            )
        )
    if not providers:
        # Data Designer refuses to start with no provider even for a column that calls no model;
        # this one is unreachable on purpose and no model config names it.
        providers.append(
            c.ModelProvider(
                name="dw-none",
                endpoint="http://127.0.0.1:9/v1",
                provider_type="openai",
                api_key=None,
            )
        )
    builder = c.DataDesignerConfigBuilder(model_configs=models)
    seed = table.select(["_dw_row_key", "_dw_occurrence"] + list(payload["seed_columns"]))
    builder.with_seed_dataset(
        c.DataFrameSeedSource(df=seed.to_pandas()), sampling_strategy=c.SamplingStrategy.ORDERED
    )
    column_cls = getattr(c, payload["column_class"])
    fields = {k: v for k, v in params.items() if k in set(payload["column_fields"])}
    if base_url is not None and "model_alias" in column_cls.model_fields:
        fields["model_alias"] = "dw"
    builder.add_column(column_cls(name=payload["output_column"], **fields))
    designer = DataDesigner(artifact_path=artifact_dir, model_providers=providers)
    results = designer.create(builder, num_records=table.num_rows)
    frame = results.load_dataset()
    keep = ["_dw_row_key", "_dw_occurrence", payload["output_column"]]
    records: list[dict[str, Any]] = frame[keep].to_dict(orient="records")
    return records


def generate(
    table: pa.Table, payload: dict[str, Any], api_key: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run the column through the relay; return (produced rows, relay records)."""
    scratch = _data_dir() / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    artifact_dir = tempfile.mkdtemp(prefix="dd-", dir=scratch)
    try:
        endpoint = payload.get("endpoint")
        if endpoint is None:
            return create_records(table, payload, None, artifact_dir), []
        target = RelayTarget(
            endpoint["base_url"], endpoint.get("model"), api_key, endpoint["is_millm"]
        )
        lease = LeaseRef(payload["lease_id"]) if payload.get("lease_id") else None
        with LoopbackRelay(
            target, body_overrides=payload.get("body_overrides"), lease=lease
        ) as relay:
            os.environ[NONCE_ENV] = relay.nonce
            try:
                produced = create_records(table, payload, relay.base_url, artifact_dir)
            finally:
                os.environ.pop(NONCE_ENV, None)
            return produced, list(relay.records)
    finally:
        shutil.rmtree(artifact_dir, ignore_errors=True)


def _produced_table(produced: list[dict[str, Any]], output_column: str) -> pa.Table:
    schema = pa.schema(
        [("_dw_row_key", pa.string()), ("_dw_occurrence", pa.int32()), (output_column, pa.string())]
    )
    rows = [
        {
            "_dw_row_key": str(r["_dw_row_key"]),
            "_dw_occurrence": int(r["_dw_occurrence"]),
            output_column: None if r.get(output_column) is None else str(r[output_column]),
        }
        for r in produced
    ]
    return pa.Table.from_pylist(rows, schema=schema)


def run_step(payload: dict[str, Any]) -> dict[str, Any]:
    destination = _confined(payload["output_dir"])
    staging = _data_dir() / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / f"dd-{uuid.uuid4().hex}"
    staged.mkdir()
    try:
        validate_params(payload)
        api_key = take_key(payload.get("key_ref"))
        table = _read_parts(payload["input_dir"])
        produced, records = generate(table, payload, api_key)
        del api_key
        pq.write_table(
            _produced_table(produced, payload["output_column"]), staged / "output.parquet"
        )
        (staged / "records.json").write_text(json.dumps(records, sort_keys=True))
    except Exception as exc:
        code = (
            str(exc).split(":", 1)[0]
            if str(exc).startswith("params_invalid")
            else "designer_failed"
        )
        (staged / "error.json").write_text(
            json.dumps(
                {
                    "code": code,
                    "message": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            )
        )
        _publish(staged, destination)
        raise
    _publish(staged, destination)
    return {"rows": table.num_rows, "produced": len(produced)}


def run_preview(payload: dict[str, Any]) -> dict[str, Any]:
    validate_params(payload)
    tables = [pq.read_table(_confined(p)) for p in payload["files"]]
    table = pa.concat_tables(tables, promote_options="default") if tables else pa.table({})
    size = min(int(payload["sample_size"]), table.num_rows)
    rng = random.Random(int(payload["seed"]))  # noqa: S311 - a reproducible sample, not a secret
    chosen = sorted(rng.sample(range(table.num_rows), size))
    sample = table.take(pa.array(chosen, pa.int64())) if size else table.slice(0, 0)
    if sample.num_rows == 0:
        return {"rows": [], "produced": [], "records": []}
    produced, records = generate(sample, payload, take_key(payload.get("key_ref")))
    return {
        "rows": sample.to_pylist(),
        "produced": _produced_table(produced, payload["output_column"]).to_pylist(),
        "records": records,
    }


app = Celery(
    "midataworks-designer",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
)
app.conf.update(
    task_default_queue=QUEUE,
    task_routes={"midataworks.designer.*": {"queue": QUEUE}},
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
)


# shared=False: never registered in the backend app, which must not believe it can run them.
@app.task(name=PING_TASK, shared=False)
def ping() -> dict[str, str]:
    return {"status": "ok"}


@app.task(name=STEP_TASK, shared=False)
def step(payload: dict[str, Any]) -> dict[str, Any]:
    return run_step(payload)


@app.task(name=PREVIEW_TASK, shared=False)
def preview(payload: dict[str, Any]) -> dict[str, Any]:
    return run_preview(payload)
