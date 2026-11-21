"""A fake runner for backend tests (feature 018; FTID §8): the real client, a scripted server.

``FakeRunner`` is an ``httpx.MockTransport`` handler that answers ``/health``, ``/v1/run`` and
``/v1/inspect`` the way the runner does - documents by hash with 409 ``document_not_cached``, 422
``text_invalid`` for a lone surrogate, diagnostics only in ``dry_run`` - and RECORDS every request
(mode, lane, limits, document hash, calls), so tests assert the payload and the call count. Each
function "runs" as a registered Python twin (``code_rule_fixtures``); the source is never executed
here. Switches: ``down`` (connection refused), ``vary_second`` (a ``repeat: 2`` run whose second
answer differs, M16), ``contract_version`` (a runner on another contract minor), ``inspect`` (an
override of the inspect answer); and, from 018 Review R2, ``starve_after`` (units past the first N
of a request read ``rule_worker_unavailable``: the runner's per-request lane backstop),
``refuse_run`` (``/v1/run`` answers a named refusal, e.g. ``limit_above_ceiling``), ``runs_down``
(``/v1/run`` refused while ``/health`` answers) and ``health_status``; and, from slice 4,
``fail_texts`` (a text answered with a reason in ``stored`` mode).

:func:`installed` swaps it into ``services.code_rules.limits`` (async and sync clients) and
restores the settings-built default afterwards.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from src.clients.runner_client import AsyncRunnerClient, RunnerClient
from src.services.code_rules import limits

Twin = Callable[[str, dict[str, Any]], int]


class FakeRunner:
    def __init__(self, twins: dict[str, dict[str, Twin]] | None = None) -> None:
        self.twins: dict[str, dict[str, Twin]] = dict(twins or {})
        self.documents: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.down = False
        self.vary_second = False
        self.contract_version = "1.0"
        self.bundle_digest = "f" * 64
        self.inspect: dict[str, Any] | None = None
        #: A runner that (wrongly) sends diagnostics in every mode: what the API keeps must not change.
        self.diagnostics_everywhere = False
        #: 018 Review R2: the lane backstop, a refused request, a runner gone after /health.
        self.starve_after: int | None = None
        self.refuse_run: tuple[int, str, str] | None = None
        self.runs_down = False
        self.health_status = "ok"
        #: 018 slice 4: a text the fake answers with this reason (e.g. rule_resource_limit) in
        #: ``stored`` mode - one row exhausting its fuel in a build (FR-018.21).
        self.fail_texts: dict[str, str] = {}

    # --- what tests read ---------------------------------------------------------------------

    def runs(self, mode: str | None = None) -> list[dict[str, Any]]:
        return [
            r["body"]
            for r in self.requests
            if r["path"] == "/v1/run" and (mode is None or r["body"]["mode"] == mode)
        ]

    # --- the server ----------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append({"path": request.url.path, "body": body})
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(
                200 if self.health_status == "ok" else 503,
                json={
                    "status": self.health_status,
                    "contract_version": self.contract_version,
                    "bundle_digest": self.bundle_digest,
                    "env_names": ["PATH"],
                },
            )
        doc_ref = body["document"]
        if "canonical_utf8" in doc_ref:
            self.documents[doc_ref["code_sha256"]] = json.loads(doc_ref["canonical_utf8"])
        document = self.documents.get(doc_ref["code_sha256"])
        if document is None:
            return httpx.Response(
                409, json={"error": {"code": "document_not_cached", "message": "resend"}}
            )
        if request.url.path == "/v1/inspect":
            if self.inspect is not None:
                return httpx.Response(200, json=self.inspect)
            return httpx.Response(
                200,
                json={
                    "functions": sorted(self.twins.get(doc_ref["code_sha256"], {})),
                    "imports": [],
                },
            )
        if self.runs_down:
            raise httpx.ConnectError("refused", request=request)
        if self.refuse_run is not None:
            status, code, message = self.refuse_run
            return httpx.Response(status, json={"error": {"code": code, "message": message}})
        twins = self.twins.get(doc_ref["code_sha256"], {})
        results = []
        units = 0
        for call in body["calls"]:
            row = []
            for text in call["texts"]:
                try:
                    text.encode("utf-8")
                except UnicodeEncodeError:
                    return httpx.Response(
                        422, json={"error": {"code": "text_invalid", "message": "surrogate"}}
                    )
                for attempt in range(call["repeat"]):
                    units += 1
                    if self.starve_after is not None and units > self.starve_after:
                        row.append({"reason": "rule_worker_unavailable"})
                        continue
                    row.append(self._one(twins, call, text, body["mode"], attempt))
            results.append(row)
        return httpx.Response(
            200,
            json={
                "contract_version": self.contract_version,
                "bundle_digest": self.bundle_digest,
                "results": results,
            },
        )

    def _one(
        self, twins: dict[str, Twin], call: dict[str, Any], text: str, mode: str, attempt: int
    ) -> dict[str, Any]:
        if mode == "stored" and text in self.fail_texts:
            return {"reason": self.fail_texts[text]}
        twin = twins.get(call["function"])
        if twin is None:
            out: dict[str, Any] = {"reason": "rule_error"}
            if mode == "dry_run":
                out["diagnostic"] = {"exception_type": "NameError", "line": 1, "message": "x"}
            return out
        try:
            count = twin(text, call["params"])
        except Exception as exc:  # noqa: BLE001 - a raising twin is a raising rule
            out = {"reason": "rule_error"}
            if mode == "dry_run" or self.diagnostics_everywhere:
                out["diagnostic"] = {
                    "exception_type": type(exc).__name__,
                    "line": 3,
                    "message": str(exc)[:200],
                }
                out["fuel_used"] = 1000
            return out
        if self.vary_second and attempt == 1:
            count += 1
        result: dict[str, Any] = {"count": min(count, 1000)}
        if mode == "dry_run":
            result["fuel_used"] = 1000 + len(text)
        return result


@contextmanager
def installed(fake: FakeRunner) -> Iterator[FakeRunner]:
    """The fake behind the API's and the workers' runner clients, for the block's duration."""

    async def no_wait(_: float) -> None:
        return None

    limits.install_client(
        lambda: AsyncRunnerClient(
            "http://fake-runner:8080",
            budget_s=0.2,
            transport=httpx.MockTransport(fake),
            sleep=no_wait,
        ),
        lambda: RunnerClient(
            "http://fake-runner:8080",
            budget_s=0.2,
            transport=httpx.MockTransport(fake),
            sleep=lambda _s: None,
        ),
    )
    try:
        yield fake
    finally:
        limits.install_client()
