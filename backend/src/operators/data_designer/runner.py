"""The Data Designer worker: step and preview tasks (FR-003.15; PADR ADR-010 amendment 2026-10-07).

Runs ONLY in the ``hitsai/midataworks-designer`` image, as a Celery worker consuming the
``designer`` queue — the Data-Juicer pattern exactly. ``data-designer==0.9.4`` needs
``huggingface-hub<2`` and the backend pins 2.2.0, so the backend never imports ``data_designer``;
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
VALIDATE_TASK = "midataworks.designer.validate"
#: Parameters Data Designer renders as Jinja templates (expression and prompt columns).
TEMPLATE_FIELDS = ("expr", "prompt", "system_prompt")
#: The record a row with no output leaves behind, by why (PII run F-3): an expression that renders
#: empty is an intended skip, not a generation failure.
DROPS_FILE = "drops.json"
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
# Declared types are kept (e2e run F1, 2026-10-21)
# --------------------------------------------------------------------------------------------

#: Data Designer modules that ``json.loads`` every string of a record before rendering it.
STRING_GUARD_MODULES = (
    "data_designer.engine.column_generators.generators.expression",
    "data_designer.engine.column_generators.generators.llm_completion",
)
#: The Arrow type a step's output column is stored as, per ``dtype``: the type the next step's
#: template sees is the type this step declared.
DTYPE_ARROW = {"str": pa.string(), "int": pa.int64(), "float": pa.float64(), "bool": pa.bool_()}
DTYPE_PYTHON: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (float, int),
    "bool": (bool,),
}


class DtypeViolation(ValueError):
    """A value whose type is not the step's declared ``dtype`` (never converted silently)."""


def keep_record_strings(record: Any) -> Any:
    """A record's values exactly as stored: a string stays a string.

    Data Designer 0.9.4 runs ``json.loads`` over every string value of a record before a template
    sees it, so ``"9"`` arrived as 9, ``"[ ]"`` as an empty list and JSON text as a dict, while
    ``"036001808"`` (not valid JSON) stayed a string - one column, mixed types, and labels that
    were silently wrong (every SSN gate row came out ``none``). Non-string values (numbers,
    lists, structs) are passed through as they are."""
    import copy

    return copy.deepcopy(record)


def install_string_guard() -> None:
    """Replace Data Designer's record deserialiser in each generator module with
    :func:`keep_record_strings` for RECORDS (dicts); anything else - a model's structured output -
    still goes to the original. Refuses loudly when a module no longer has the function: a
    silently skipped guard would bring the coercion back."""
    import importlib

    for name in STRING_GUARD_MODULES:
        module = importlib.import_module(name)
        current = getattr(module, "deserialize_json_values", None)
        if current is None:
            raise RuntimeError(
                f"{name} no longer has deserialize_json_values: Data Designer changed, and the "
                "guard that keeps declared strings strings cannot be installed. Refusing to run."
            )
        if getattr(current, "__dw_keeps_strings__", False):
            continue

        def guarded(data: Any, _original: Any = current) -> Any:
            if isinstance(data, dict):
                return keep_record_strings(data)
            return _original(data)

        guarded.__dw_keeps_strings__ = True  # type: ignore[attr-defined]
        module.deserialize_json_values = guarded  # type: ignore[attr-defined]


def check_dtypes(produced: list[dict[str, Any]], output_column: str, dtype: str) -> None:
    """Every produced value has the step's declared type, or the step fails naming the row."""
    allowed = DTYPE_PYTHON.get(dtype, (str,))
    for record in produced:
        value = record.get(output_column)
        if value is None:
            continue
        if isinstance(value, bool) and dtype != "bool":
            ok = False
        else:
            ok = isinstance(value, allowed)
        if not ok:
            raise DtypeViolation(
                f"dtype_violation: row {record.get('_dw_row_key')} produced "
                f"{type(value).__name__} {value!r} for the {dtype} column {output_column!r}"
            )


def output_dtype(payload: dict[str, Any]) -> str:
    """The declared dtype of an expression step; every other column type is text."""
    if payload.get("column_class") != "ExpressionColumnConfig":
        return "str"
    return str(payload["params"].get("dtype") or "str")


# --------------------------------------------------------------------------------------------
# Running one column
# --------------------------------------------------------------------------------------------


@dataclass
class RelayTarget:
    base_url: str
    model: str | None
    api_key: str | None = field(repr=False)
    is_millm: bool
    key_origin: str | None = None


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
    seed = table.select(["_dw_row_key", "_dw_occurrence"] + visible_seed_columns(table, payload))
    builder.with_seed_dataset(
        c.DataFrameSeedSource(df=seed.to_pandas()), sampling_strategy=c.SamplingStrategy.ORDERED
    )
    column_cls = getattr(c, payload["column_class"])
    fields = {k: v for k, v in params.items() if k in set(payload["column_fields"])}
    if base_url is not None and "model_alias" in column_cls.model_fields:
        fields["model_alias"] = "dw"
    builder.add_column(column_cls(name=payload["output_column"], **fields))
    install_string_guard()
    designer = DataDesigner(artifact_path=artifact_dir, model_providers=providers)
    results = designer.create(builder, num_records=table.num_rows)
    frame = results.load_dataset()
    keep = ["_dw_row_key", "_dw_occurrence", payload["output_column"]]
    records: list[dict[str, Any]] = frame[keep].to_dict(orient="records")
    return records


def template_references(params: dict[str, Any]) -> set[str]:
    """Every top-level name the step's templates read (Jinja's own undeclared-variable scan)."""
    from jinja2 import Environment, meta
    from jinja2.exceptions import TemplateSyntaxError

    names: set[str] = set()
    env = Environment(autoescape=False)  # noqa: S701 - parsed for names only, never rendered
    for field_name in TEMPLATE_FIELDS:
        value = params.get(field_name)
        if not isinstance(value, str):
            continue
        try:
            names |= set(meta.find_undeclared_variables(env.parse(value)))
        except TemplateSyntaxError:
            continue  # Data Designer reports the syntax error itself
    return names


def visible_seed_columns(table: pa.Table, payload: dict[str, Any]) -> list[str]:
    """The columns Data Designer is seeded with (PII run G5/L2, 2026-10-20).

    The content columns, as before, PLUS any other non-system column a template names: a metadata
    column or an earlier ``dd_*`` step's output (always metadata). Before, only content columns
    were seeded, so ``{{ uid }}`` failed as an unknown reference and chained stages needed a new
    version each. ``_dw_*`` system columns are never visible. A payload from a backend that sends
    no ``visible_columns`` keeps the content-only behaviour.
    """
    seed = list(payload["seed_columns"])
    visible = set(payload.get("visible_columns") or [])
    wanted = template_references(payload["params"]) & visible
    for column in sorted(wanted):
        if column not in seed and column in table.schema.names and not column.startswith("_dw_"):
            seed.append(column)
    return seed


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def classify_drops(
    table: pa.Table, payload: dict[str, Any], produced: list[dict[str, Any]]
) -> dict[str, str]:  # pragma: no cover - exercised in designer/tests with the real library
    """``{"<row key>|<occurrence>": reason}`` for every row an EXPRESSION column produced nothing
    for, re-rendered through Data Designer's own sandbox (PII run F-3).

    Data Designer's expression generator drops a row for three reasons and logs only the counts:
    an empty render (``empty_render``, usually intended: the template emits nothing for rows it
    should skip), a render error (``render_error``) and a failed cast to ``dtype``
    (``type_cast_error``). A row that renders and casts here was dropped for a reason this check
    cannot see (``not_generated``).
    """
    if payload.get("column_class") != "ExpressionColumnConfig":
        return {}
    from data_designer.engine.processing.ginja.environment import UserTemplateSandboxEnvironment
    from data_designer.engine.processing.ginja.exceptions import EmptyTemplateRenderError

    got = {(str(r["_dw_row_key"]), int(r["_dw_occurrence"])) for r in produced}
    columns = visible_seed_columns(table, payload)
    env = UserTemplateSandboxEnvironment(allowed_references=columns)
    expr = str(payload["params"].get("expr", ""))
    dtype = str(payload["params"].get("dtype") or "str")
    casts: dict[str, Any] = {"str": str, "float": float, "int": lambda v: int(float(v))}
    out: dict[str, str] = {}
    for row in table.select(["_dw_row_key", "_dw_occurrence", *columns]).to_pylist():
        pair = (str(row["_dw_row_key"]), int(row["_dw_occurrence"]))
        if pair in got:
            continue
        record = keep_record_strings({c: row[c] for c in columns})
        try:
            value = env.safe_render(expr, record)
        except EmptyTemplateRenderError:
            reason = "empty_render"
        except Exception:  # noqa: BLE001 - Data Designer drops the row for any render error
            reason = "render_error"
        else:
            if _empty(value):
                reason = "empty_render"
            else:
                try:
                    casts.get(dtype, str)(value)
                    reason = "not_generated"
                except (OverflowError, TypeError, ValueError):
                    reason = "type_cast_error"
        out[f"{pair[0]}|{pair[1]}"] = reason
    return out


def validate_templates(payload: dict[str, Any]) -> dict[str, Any]:
    """Data Designer's OWN template rules for the step's templates, naming the rule that failed
    (PII run G4, 2026-10-20). Without this, the backend said "valid" for a template Data
    Designer then refused with only "User provided prompt generation template is invalid".

    Each rule is checked in Data Designer's order with Data Designer's own environment, limits
    and AST helpers; then its ``validate_template`` and a compile run as the authority, so a rule
    this function does not name still refuses (``rule: data_designer``). With ``columns`` (the
    columns the step will see) unknown references are checked too; without it they are listed.
    """
    from data_designer.engine.processing.ginja.ast import (
        ast_count_name_references,
        ast_descendant_count,
        ast_max_depth,
    )
    from data_designer.engine.processing.ginja.environment import (
        ALLOWED_JINJA_FILTERS,
        MAX_AST_DEPTH,
        MAX_AST_NODE_COUNT,
        UNSUPPORTED_AST_NODES,
        UserTemplateSandboxEnvironment,
    )
    from jinja2 import nodes as j
    from jinja2.exceptions import TemplateSyntaxError

    params = dict(payload.get("params") or {})
    columns = payload.get("columns")
    errors: list[dict[str, Any]] = []
    references: dict[str, list[str]] = {}
    tags = {
        j.Import: "{% import %} / {% include %}",
        j.Macro: "{% macro %}",
        j.Assign: "{% set %}",
        j.Extends: "{% extends %}",
        j.Block: "{% block %}",
    }
    for field_name in TEMPLATE_FIELDS:
        template = params.get(field_name)
        if not isinstance(template, str):
            continue
        env = UserTemplateSandboxEnvironment(
            allowed_references=list(columns) if columns is not None else None
        )

        def fail(rule: str, message: str, field: str = field_name, **extra: Any) -> None:
            errors.append({"field": field, "rule": rule, "message": message, **extra})

        try:
            ast = env.parse(template)
        except TemplateSyntaxError as exc:
            fail("syntax", f"Not a valid Jinja2 template (line {exc.lineno}): {exc.message}")
            continue
        found_tags = sorted(
            {name for node_type, name in tags.items() if ast_descendant_count(ast, node_type)}
            | ({"{% include %}"} if ast_descendant_count(ast, j.Include) else set())
        )
        if any(ast_descendant_count(ast, t) for t in UNSUPPORTED_AST_NODES) or found_tags:
            fail(
                "forbidden_tag",
                "Data Designer forbids " + ", ".join(found_tags) + " in a template.",
                found=found_tags,
            )
        if any(node.recursive for node in ast.find_all(j.For)):
            fail("recursive_for", "Data Designer forbids {% for ... recursive %}.")
        if any(ast_descendant_count(node, only_type=j.For) for node in ast.find_all(j.For)):
            fail("nested_for", "Data Designer forbids a {% for %} loop inside another loop.")
        nodes = ast_descendant_count(ast)
        depth = ast_max_depth(ast)
        if nodes > MAX_AST_NODE_COUNT:
            fail(
                "too_many_nodes",
                f"The template has {nodes} nodes; Data Designer allows at most "
                f"{MAX_AST_NODE_COUNT}. Split it across two steps.",
                found=nodes,
                limit=MAX_AST_NODE_COUNT,
            )
        if depth > MAX_AST_DEPTH:
            fail(
                "too_deep",
                f"The template nests {depth} deep; Data Designer allows at most {MAX_AST_DEPTH}. "
                "Flatten conditions or compute a part in an earlier step.",
                found=depth,
                limit=MAX_AST_DEPTH,
            )
        if ast_count_name_references(ast, "self"):
            fail("self_reference", "Data Designer forbids {{ self }}.")
        filters = sorted({f.name for f in ast.find_all(j.Filter)} - set(ALLOWED_JINJA_FILTERS))
        if filters:
            fail(
                "forbidden_filter",
                "Data Designer allows only these filters: "
                + ", ".join(ALLOWED_JINJA_FILTERS)
                + f". Not allowed here: {', '.join(filters)}.",
                found=filters,
                allowed=list(ALLOWED_JINJA_FILTERS),
            )
        # Names are read with a plain environment: ``meta`` compiles the AST, and Data
        # Designer's environment would raise on a forbidden filter already reported above.
        named = sorted(template_references({field_name: template}))
        references[field_name] = named
        if columns is not None:
            unknown = sorted(set(named) - set(columns))
            if unknown:
                fail(
                    "unknown_reference",
                    f"The template names {unknown}, which the step will not see. It sees "
                    f"{sorted(columns)}.",
                    found=unknown,
                )
        if not [e for e in errors if e["field"] == field_name]:
            try:  # the authority: Data Designer's own check, then a compile (filters bind there)
                env.validate_template(template)
                env.from_string(template)
            except Exception as exc:  # noqa: BLE001 - reported with Data Designer's message
                fail("data_designer", f"Data Designer refused the template: {exc}")
    if not errors:
        import data_designer.config as c

        column_cls = getattr(c, payload["column_class"])
        fields = {k: v for k, v in params.items() if k in set(payload.get("column_fields") or [])}
        try:
            column_cls(name=str(payload.get("output_column") or "out"), **fields)
        except Exception as exc:  # noqa: BLE001 - the column config's own validation
            errors.append({"field": None, "rule": "config", "message": str(exc)[:1000]})
    return {
        "valid": not errors,
        "errors": errors,
        "references": references,
        "limits": {
            "max_nodes": MAX_AST_NODE_COUNT,
            "max_depth": MAX_AST_DEPTH,
            "filters": list(ALLOWED_JINJA_FILTERS),
        },
    }


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
            endpoint["base_url"],
            endpoint.get("model"),
            api_key,
            endpoint["is_millm"],
            endpoint.get("key_origin"),
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


def _produced_table(
    produced: list[dict[str, Any]], output_column: str, dtype: str = "str"
) -> pa.Table:
    """The produced rows with the output column stored as its declared type (e2e run F1): a str
    column is text, an int column int64, and so on, so the next step reads what this one meant.
    A value of another type fails the step (:func:`check_dtypes`); nothing is converted."""
    check_dtypes(produced, output_column, dtype)
    schema = pa.schema(
        [
            ("_dw_row_key", pa.string()),
            ("_dw_occurrence", pa.int32()),
            (output_column, DTYPE_ARROW.get(dtype, pa.string())),
        ]
    )
    rows = [
        {
            "_dw_row_key": str(r["_dw_row_key"]),
            "_dw_occurrence": int(r["_dw_occurrence"]),
            output_column: r.get(output_column),
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
            _produced_table(produced, payload["output_column"], output_dtype(payload)),
            staged / "output.parquet",
        )
        (staged / "records.json").write_text(json.dumps(records, sort_keys=True))
        (staged / DROPS_FILE).write_text(
            json.dumps(classify_drops(table, payload, produced), sort_keys=True)
        )
    except Exception as exc:
        code = (
            str(exc).split(":", 1)[0]
            if str(exc).startswith(("params_invalid", "dtype_violation"))
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
        return {"rows": [], "produced": [], "records": [], "drops": {}}
    produced, records = generate(sample, payload, take_key(payload.get("key_ref")))
    return {
        "rows": sample.to_pylist(),
        "produced": _produced_table(
            produced, payload["output_column"], output_dtype(payload)
        ).to_pylist(),
        "records": records,
        "drops": classify_drops(sample, payload, produced),
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


@app.task(name=VALIDATE_TASK, shared=False)
def validate(payload: dict[str, Any]) -> dict[str, Any]:
    return validate_templates(payload)
