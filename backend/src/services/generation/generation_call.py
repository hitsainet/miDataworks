"""One generation-call interface, two transports (FTID 007 section 3.1; FTDD 007 section 6.3).

``GenerationCall.run(requests, ctx)`` returns one :class:`GenerationResult` per request. Every
caller (the engine and the preview) then applies ``steering.check_reported_state`` to the
result's VERBATIM header, so the steering check cannot differ between the transports:

- :class:`NativeGenerationCall` posts each request through 005's ``EndpointCaller`` with purpose
  ``generation`` (``X-miLLM-Strict: true``, ``X-miLLM-Load-Policy: refuse``, the lease header when
  pinned; 503 waits honour ``Retry-After`` and never count as failures).
- :class:`RelayGenerationCall` runs 007's ``native_chat_generate`` operator through 003's
  ``execute_in_process`` with a ``GenerationStageSpec``: each request goes through the loopback
  relay with its key in ``X-Dataworks-Row-Key``, the side's ``body_overrides`` are merged by the
  relay, and the relay's per-request records (status, model, raw ``X-miLLM-Steering``, seed echo)
  are joined back by that key. Data Designer itself cannot carry a per-row key (003 spike 1.4:
  ``extra_body`` is static), so this is the relay path that exists (FTASKS 1.5).

The path is fixed per run at plan time (``GENERATION_ENGINE_PATH``) and never switches mid-run.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ...clients.endpoint_caller import SEED_HEADER, STEERING_HEADER, EndpointCaller
from ...clients.endpoint_errors import (
    ContextOverflow,
    EndpointCallError,
    LeaseLost,
    ModelNotResident,
    StrictRefusal,
)

Outcome = Literal["ok", "context_overflow", "parse_failure", "error"]


class CallStop(Exception):
    """A response that must stop the run (strict refusal, model not resident, lease lost)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class GenerationRequest:
    record_index: int
    row_key: str
    messages: list[dict[str, Any]]
    sampling: dict[str, Any]
    seed: int | None
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class GenerationResult:
    record_index: int
    text: str | None
    finish_reason: str | None
    model: str | None
    #: ``X-miLLM-Steering`` verbatim; None when absent (never a guess).
    steering_header: str | None
    #: The seed miLLM echoed in ``X-miLLM-Seed``; None when not echoed.
    seed_echo: int | None
    latency_ms: int
    outcome: Outcome
    error: str | None = None


@dataclass
class CallContext:
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    lease_id: str | None = field(default=None, repr=False)
    is_millm: bool = False
    body_overrides: dict[str, Any] = field(default_factory=dict)
    sleep: Callable[[float], None] | None = None
    on_wait: Callable[[float, str], None] | None = None
    timeout_s: float = 120.0
    job_id: str = "preview"


class GenerationCall(Protocol):
    def run(
        self, requests: Sequence[GenerationRequest], ctx: CallContext
    ) -> list[GenerationResult]: ...


def request_body(req: GenerationRequest, model: str) -> dict[str, Any]:
    """The chat-completions body (without the side's overrides)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": req.messages,
        "temperature": float(req.sampling.get("temperature", 0.8)),
        "top_p": float(req.sampling.get("top_p", 1.0)),
        "max_tokens": int(req.sampling.get("max_tokens", 512)),
    }
    if req.seed is not None:
        body["seed"] = int(req.seed)
    if req.response_format is not None:
        body["response_format"] = req.response_format
    return body


def parse_seed_echo(value: str | None) -> int | None:
    """``X-miLLM-Seed: 7;scope="request"`` → 7 (miLLM FR-25.13.6); None when absent or unreadable."""
    if not value:
        return None
    head = value.split(";", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _choice(body: Any) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, None
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, None
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    finish = choices[0].get("finish_reason")
    return (str(text) if text is not None else None), (str(finish) if finish else None)


def _parsed(text: str | None, req: GenerationRequest) -> Outcome:
    if req.response_format is None or text is None:
        return "ok"
    try:
        json.loads(text)
    except ValueError:
        return "parse_failure"
    return "ok"


class NativeGenerationCall:
    """One ``POST /v1/chat/completions`` per request through 005's ``EndpointCaller``."""

    def __init__(self, caller_factory: Callable[[CallContext], EndpointCaller]) -> None:
        self._factory = caller_factory

    def run(
        self, requests: Sequence[GenerationRequest], ctx: CallContext
    ) -> list[GenerationResult]:
        out: list[GenerationResult] = []
        with self._factory(ctx) as caller:
            for req in requests:
                body = {**request_body(req, ctx.model), **ctx.body_overrides}
                started = time.perf_counter()
                try:
                    response = caller.call(
                        "POST",
                        "/v1/chat/completions",
                        body=body,
                        purpose="generation",
                        openai=True,
                        lease_id=ctx.lease_id,
                    )
                except ContextOverflow as exc:
                    out.append(_failed(req, "context_overflow", exc.message, started))
                    continue
                except (StrictRefusal, ModelNotResident, LeaseLost) as exc:
                    raise CallStop(exc.code, exc.message) from None
                except EndpointCallError as exc:
                    out.append(_failed(req, "error", f"{exc.code}: {exc.message}", started))
                    continue
                text, finish = _choice(response.body)
                model = response.body.get("model") if isinstance(response.body, dict) else None
                out.append(
                    GenerationResult(
                        record_index=req.record_index,
                        text=text,
                        finish_reason=finish,
                        model=str(model) if model is not None else None,
                        steering_header=response.header(STEERING_HEADER),
                        seed_echo=parse_seed_echo(response.header(SEED_HEADER)),
                        latency_ms=response.latency_ms,
                        outcome=_parsed(text, req),
                    )
                )
        return out


def _failed(
    req: GenerationRequest, outcome: Outcome, error: str, started: float
) -> GenerationResult:
    return GenerationResult(
        record_index=req.record_index,
        text=None,
        finish_reason=None,
        model=None,
        steering_header=None,
        seed_echo=None,
        latency_ms=int((time.perf_counter() - started) * 1000),
        outcome=outcome,
        error=error,
    )


# --- the relay path -------------------------------------------------------------------------

#: The operator the relay path runs (``operators/native/generation.py``).
RELAY_OPERATOR = "native_chat_generate"
RELAY_OPERATOR_VERSION = "1"


class RelayGenerationCall:
    """The requests as one generation stage of ``native_chat_generate`` on the loopback relay."""

    def __init__(self, execute: Callable[..., Any] | None = None, registry: Any = None) -> None:
        self._execute = execute
        self._registry = registry

    def run(
        self, requests: Sequence[GenerationRequest], ctx: CallContext
    ) -> list[GenerationResult]:
        import pyarrow as pa

        from ...core.storage import data_dir, tmp_dir
        from ...operators import executor
        from ...operators.native.generation import REQUEST_COLUMN
        from ...operators.registry import current
        from ...services.row_keys import compute_row_key

        if not requests:
            return []
        registry = self._registry or current()
        entry = registry.require_allowed(RELAY_OPERATOR, RELAY_OPERATOR_VERSION)
        rows = []
        by_key: dict[str, GenerationRequest] = {}
        for req in requests:
            body = request_body(req, ctx.model)
            # The record index rides beside the body (never inside it: strict mode would refuse
            # an unknown field), so two identical bodies still get distinct keys.
            payload = json.dumps(
                {"record_index": req.record_index, "body": body},
                sort_keys=True,
                ensure_ascii=False,
            )
            key = compute_row_key({REQUEST_COLUMN: payload}, [REQUEST_COLUMN])
            by_key[key] = req
            rows.append({"_dw_row_key": key, "_dw_occurrence": 0, REQUEST_COLUMN: payload})
        table = pa.Table.from_pylist(
            rows,
            schema=pa.schema(
                [
                    ("_dw_row_key", pa.string()),
                    ("_dw_occurrence", pa.int64()),
                    (REQUEST_COLUMN, pa.string()),
                ]
            ),
        )
        out_dir = tmp_dir() / f"gen-stage-{uuid.uuid4().hex}"
        relative = str(out_dir.relative_to(data_dir()))
        spec = executor.GenerationStageSpec(
            job_id=ctx.job_id,
            operator=RELAY_OPERATOR,
            version=RELAY_OPERATOR_VERSION,
            expected_manifest_hash=str(entry.manifest_hash),
            params={},
            output_dir=relative,
            step_seed=0,
            column_roles={REQUEST_COLUMN: "content"},
            rowkey_scheme="dw.rowkey/v1",
            input_table=table,
            body_overrides=dict(ctx.body_overrides) or None,
            lease_id=ctx.lease_id,
        )
        run = self._execute or executor.execute_in_process
        started = time.perf_counter()
        try:
            result = run(spec, registry=registry)
            output = {r["_dw_row_key"]: r for r in _read_output(relative)}
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
        records = {str(r.get("row_key")): r for r in result.relay_records}
        out: list[GenerationResult] = []
        latency = int((time.perf_counter() - started) * 1000)
        for key, req in by_key.items():
            record = records.get(key) or {}
            row = output.get(key) or {}
            status = int(record.get("status") or row.get("status") or 0)
            reason = record.get("reason")
            text = row.get("text")
            error_code = row.get("error_code")
            if error_code in ("model_not_resident", "model_leased", "unused_fields_refused"):
                code = {
                    "model_not_resident": "MODEL_NOT_LOADED",
                    "model_leased": "LEASE_LOST",
                    "unused_fields_refused": "STRICT_REFUSAL",
                }[str(error_code)]
                raise CallStop(code, str(row.get("error") or error_code))
            if reason == "context_overflow":
                outcome: Outcome = "context_overflow"
            elif status != 200:
                outcome = "error"
            else:
                outcome = _parsed(text, req)
            out.append(
                GenerationResult(
                    record_index=req.record_index,
                    text=text if status == 200 else None,
                    finish_reason=row.get("finish_reason"),
                    model=record.get("model") or row.get("model"),
                    steering_header=record.get("steering_header"),
                    seed_echo=parse_seed_echo(record.get("seed_header")),
                    latency_ms=int(record.get("latency_ms") or latency),
                    outcome=outcome,
                    error=None if status == 200 else str(row.get("error") or f"http_{status}"),
                )
            )
        out.sort(key=lambda r: r.record_index)
        return out


def _read_output(relative: str) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    from ...core.storage import resolve_under_data_dir
    from ...services.step_contract import part_files

    rows: list[dict[str, Any]] = []
    for path in part_files(resolve_under_data_dir(relative)):
        rows.extend(pq.read_table(path).to_pylist())
    return rows
