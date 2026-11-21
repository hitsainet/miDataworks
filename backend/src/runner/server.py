"""The runner's HTTP surface (FTDD §6.2): ``GET /health``, ``POST /v1/run``, ``POST /v1/inspect``.

Internal only (a ClusterIP Service, admitted by the NetworkPolicy; no token: a token would be a secret
the runner must not hold). It never logs a text, a source or a parameter (FR-018.25): uvicorn's access
log is off and the exception handler logs the exception type and the request's ``code_sha256`` only.
``diagnostic`` and ``fuel_used`` leave the runner only in ``dry_run`` (the caller's own text); every
other mode strips them here, not in the guest (M8).
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from src.runner_contract.reasons import Reason

from . import bundle as bundle_mod
from .config import RunnerConfig
from .documents import Document, DocumentCache, DocumentRefused, parse
from .host import Host, Limits, Outcome, guest_request
from .lanes import Lane, LaneFull

log = logging.getLogger("midataworks.runner")

MODES = ("stored", "dry_run", "save_check")
#: The request limit each RUNNER_MAX_* ceiling bounds.
CEILINGS = {
    "call_fuel": "max_call_fuel",
    "load_fuel": "max_load_fuel",
    "prelude_fuel": "max_prelude_fuel",
    "memory_bytes": "max_memory_bytes",
    "input_code_points": "max_input_code_points",
    "output_bytes": "max_output_bytes",
    "wall_ms": "max_wall_ms",
}


class Refusal(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _unavailable() -> dict[str, Any]:
    return {"reason": Reason.RULE_WORKER_UNAVAILABLE.value}


@dataclass
class Unit:
    function: str
    params: dict[str, Any]
    text: str


class Runner:
    """Everything one runner process holds: config, contract, host, lanes, document cache."""

    def __init__(self, config: RunnerConfig) -> None:
        self.config = config
        contract = bundle_mod.read_contract(config.contract_path)
        self.engine = Host.engine_for(contract)
        loaded = bundle_mod.load(self.engine, config.bundle_dir, config.contract_path)
        self.status = loaded.status
        self.contract = loaded.contract
        self.format: str = self.contract["format"]
        self.floor: dict[str, int] = dict(self.contract["limits_floor"])
        self.request_limits: tuple[str, ...] = tuple(self.contract["request_limits"])
        self.host = Host(self.engine, loaded.module, self.contract) if loaded.module else None
        self.documents = DocumentCache(config.document_cache)
        self.lanes = {
            "interactive": Lane("interactive", config.interactive_slots),
            "batch": Lane("batch", config.batch_slots),
        }
        if self.status != "ok":
            log.error("bundle refused: %s", loaded.detail)

    # --- validation ---------------------------------------------------------------------------

    def _contract(self, body: dict[str, Any]) -> None:
        if body.get("contract") != self.format:
            raise Refusal("runner_contract_unknown", "this runner serves " + self.format)

    def limits(self, raw: Any) -> Limits:
        if not isinstance(raw, dict) or set(raw) != set(self.request_limits):
            raise Refusal(
                "request_invalid", "limits must name exactly " + ", ".join(self.request_limits)
            )
        values: dict[str, int] = {}
        for name in self.request_limits:
            value = raw[name]
            if type(value) is not int or value < 1:
                raise Refusal("request_invalid", f"limits.{name} must be a positive integer")
            floor = self.floor.get(name)
            if floor is not None and value < floor:
                raise Refusal("limit_below_floor", f"limits.{name} is below the floor {floor}")
            ceiling = getattr(self.config, CEILINGS[name])
            if value > ceiling:
                raise Refusal(
                    "limit_above_ceiling", f"limits.{name} is above this runner's ceiling"
                )
            values[name] = value
        return Limits(**values)

    def floor_limits(self) -> Limits:
        return Limits(
            **{k: v for k, v in self.floor.items() if k in self.request_limits},
            wall_ms=min(10_000, self.config.max_wall_ms),
        )

    def document(self, raw: Any) -> Document:
        if not isinstance(raw, dict) or not isinstance(raw.get("code_sha256"), str):
            raise Refusal("request_invalid", "document.code_sha256 is required")
        sha = raw["code_sha256"]
        canonical = raw.get("canonical_utf8")
        if canonical is None:
            doc = self.documents.get(sha)
            if doc is None:
                raise Refusal("document_not_cached", "resend with canonical_utf8", 409)
            return doc
        if not isinstance(canonical, str):
            raise Refusal("request_invalid", "document.canonical_utf8 must be a string")
        try:
            doc = parse(sha, canonical, self.format, self.config.max_source_bytes)
        except UnicodeEncodeError as exc:
            raise Refusal("text_invalid", "document is not valid UTF-8") from exc
        except DocumentRefused as exc:
            raise Refusal(exc.code, str(exc), exc.status) from exc
        self.documents.put(doc)
        return doc

    def units(self, calls: Any, doc: Document) -> list[tuple[Unit, int]]:
        if not isinstance(calls, list) or not calls:
            raise Refusal("request_invalid", "calls must be a non-empty list")
        out: list[tuple[Unit, int]] = []
        for i, call in enumerate(calls):
            if not isinstance(call, dict) or set(call) - {"function", "params", "texts", "repeat"}:
                raise Refusal("request_invalid", f"calls[{i}] malformed")
            function, params = call.get("function"), call.get("params")
            texts, repeat = call.get("texts"), call.get("repeat", 1)
            if function not in doc.functions:
                raise Refusal("function_not_declared", f"calls[{i}].function is not declared")
            if (
                not isinstance(params, dict)
                or not isinstance(texts, list)
                or type(repeat) is not int
                or repeat not in (1, 2)
            ):
                raise Refusal("request_invalid", f"calls[{i}] malformed")
            for text in texts:
                if not isinstance(text, str):
                    raise Refusal("request_invalid", f"calls[{i}].texts must be strings")
                try:
                    text.encode("utf-8", errors="strict")
                except UnicodeEncodeError as exc:
                    raise Refusal(
                        "text_invalid",
                        "a text holds a lone surrogate; replace it with U+FFFD before sending",
                    ) from exc
                for _ in range(repeat):
                    out.append((Unit(function, params, text), i))
        if len(out) > self.config.batch_texts * 2:
            raise Refusal("request_invalid", "too many texts in one request", 413)
        return out

    # --- execution ----------------------------------------------------------------------------

    def _result(self, outcome: Outcome, mode: str) -> dict[str, Any]:
        reduced = outcome.reduced
        result: dict[str, Any]
        if reduced.reason is not None:
            result = {"reason": reduced.reason.value}
        elif reduced.count is not None:
            result = {"count": reduced.count}
        else:
            result = {"reason": Reason.RULE_OUTPUT_INVALID.value}
        if mode == "dry_run":
            diag = reduced.diag
            if diag is None and reduced.refused is not None:
                diag = {
                    "exception_type": "LoadRefused",
                    "line": None,
                    "message": f"{reduced.refused!r} is refused"[:200],
                }
            if diag is not None:
                result["diagnostic"] = diag
            result["fuel_used"] = outcome.fuel_used
        return result

    def execute(
        self, doc: Document, units: list[tuple[Unit, int]], limits: Limits, mode: str, lane: Lane
    ) -> list[list[dict[str, Any]]]:
        n_calls = max(i for _, i in units) + 1
        results: list[list[dict[str, Any]]] = [[] for _ in range(n_calls)]
        if self.status != "ok" or self.host is None:
            for _, i in units:
                results[i].append(_unavailable())
            return results
        host = self.host
        deadline = time.monotonic() + limits.wall_ms / 1000
        pending: list[tuple[int, Future[Outcome] | dict[str, Any]]] = []
        for unit, i in units:
            if len(unit.text) > limits.input_code_points:
                pending.append((i, {"reason": Reason.RULE_INPUT_TOO_LARGE.value}))
                continue
            request = guest_request(
                mode="run",
                source=doc.source,
                function=unit.function,
                params=unit.params,
                text=unit.text,
                diagnostics=mode == "dry_run",
            )
            work: Callable[[], Outcome] = functools.partial(host.run_one, request, limits)
            try:
                pending.append((i, lane.submit(work, deadline - time.monotonic())))
            except LaneFull:
                pending.append((i, _unavailable()))
        for i, item in pending:
            if isinstance(item, dict):
                results[i].append(item)
            else:
                results[i].append(self._result(item.result(), mode))
        return results

    def inspect(self, doc: Document) -> dict[str, Any]:
        if self.status != "ok" or self.host is None:
            return _unavailable()
        host = self.host
        limits = self.floor_limits()
        request = guest_request(
            mode="inspect",
            source=doc.source,
            function=None,
            params=None,
            text=None,
            diagnostics=True,
        )
        try:
            future = self.lanes["interactive"].submit(
                lambda: host.run_one(request, limits, inspect=True), limits.wall_ms / 1000
            )
        except LaneFull:
            return _unavailable()
        reduced = future.result().reduced
        if reduced.inspect is not None:
            return dict(reduced.inspect)
        out: dict[str, Any] = {
            "reason": (reduced.reason or Reason.RULE_OUTPUT_INVALID).value,
        }
        if reduced.refused is not None:
            out["refused"] = reduced.refused
        if reduced.diag is not None:
            out["diagnostic"] = reduced.diag
        return out

    def health(self) -> dict[str, Any]:
        bundle = self.contract["guest"]["bundle"]
        return {
            "status": self.status,
            "contract": self.format,
            "contract_version": self.contract["contract_version"],
            "contract_majors": [self.contract["contract_major"]],
            "bundle_digest": bundle["bundle_digest"],
            "runtime": {"family": "wasmtime", "major": self.contract["runtime"]["major"]},
            "env_names": sorted(os.environ),
            "lanes": {name: lane.snapshot() for name, lane in self.lanes.items()},
        }


async def read_bounded(stream: AsyncIterator[bytes], bound: int) -> bytes:
    """The body, refused (413 ``body_too_large``) the moment it passes ``bound`` bytes.

    Bounded WHILE reading (018 Review R1 L6): a chunked body carries no ``Content-Length``, so the
    bound is applied to the bytes as they arrive, never after the whole body is in memory.
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in stream:
        size += len(chunk)
        if size > bound:
            raise Refusal("body_too_large", "request body over the runner's bound", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(config: RunnerConfig | None = None) -> Starlette:
    runner = Runner(config or RunnerConfig.from_env())
    cfg = runner.config
    body_bound = cfg.batch_texts * cfg.max_input_code_points * 4 + 2 * cfg.max_source_bytes + 65_536

    async def read_json(request: Request) -> Any:
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > body_bound):
            raise Refusal("body_too_large", "request body over the runner's bound", 413)
        raw = await read_bounded(request.stream(), body_bound)
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise Refusal("request_invalid", "body is not JSON") from exc

    async def health(_: Request) -> JSONResponse:
        body = runner.health()
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    async def run(request: Request) -> JSONResponse:
        sha = None
        try:
            body = await read_json(request)
            if not isinstance(body, dict):
                raise Refusal("request_invalid", "body must be an object")
            runner._contract(body)
            mode, lane = body.get("mode"), body.get("lane")
            if mode not in MODES or lane not in runner.lanes:
                raise Refusal("request_invalid", "mode or lane unknown")
            limits = runner.limits(body.get("limits"))
            doc = runner.document(body.get("document"))
            sha = doc.code_sha256
            units = runner.units(body.get("calls"), doc)
            results = await anyio.to_thread.run_sync(
                runner.execute, doc, units, limits, mode, runner.lanes[lane]
            )
        except Refusal as exc:
            return _error(exc.code, str(exc), exc.status)
        except Exception as exc:  # noqa: BLE001 - logged by type and hash only (FR-018.25)
            log.error("run failed: %s (code_sha256=%s)", type(exc).__name__, sha)
            return _error("runner_internal", "the runner failed; see its log", 500)
        return JSONResponse(
            {
                "contract_version": runner.contract["contract_version"],
                "bundle_digest": runner.contract["guest"]["bundle"]["bundle_digest"],
                "results": results,
            }
        )

    async def inspect(request: Request) -> JSONResponse:
        sha = None
        try:
            body = await read_json(request)
            if not isinstance(body, dict):
                raise Refusal("request_invalid", "body must be an object")
            runner._contract(body)
            doc = runner.document(body.get("document"))
            sha = doc.code_sha256
            answer = await anyio.to_thread.run_sync(runner.inspect, doc)
        except Refusal as exc:
            return _error(exc.code, str(exc), exc.status)
        except Exception as exc:  # noqa: BLE001
            log.error("inspect failed: %s (code_sha256=%s)", type(exc).__name__, sha)
            return _error("runner_internal", "the runner failed; see its log", 500)
        return JSONResponse(answer)

    app = Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/v1/run", run, methods=["POST"]),
            Route("/v1/inspect", inspect, methods=["POST"]),
        ]
    )
    app.state.runner = runner
    return app


def main() -> None:
    import uvicorn

    config = RunnerConfig.from_env()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    uvicorn.run(
        "src.runner.server:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        workers=config.processes,
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
