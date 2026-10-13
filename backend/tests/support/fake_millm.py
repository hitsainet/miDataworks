"""A scripted fake miLLM (and TEI) behind ``httpx.MockTransport`` (FTID 005 section 8).

It serves the routes feature 005 calls, with the bodies of miLLM contract v1.11:
``GET /api/health/detailed`` (``inference`` + ``lease``), ``GET /api/models`` (``{success, data}``;
the resident model is the item whose status is ``loaded``), ``GET /v1/models``,
``POST /v1/completions`` (scoring mode), ``POST /v1/chat/completions`` (judge and chat scoring),
the lease routes and ``POST /v1/batches/{id}/lease``.

Scripted behaviour, each driven by a field the test sets:
- ``p_true``: text → P before the template's bias and temperature (default from a hash);
- ``busy``: a queue of ``Retry-After`` values (None = no header) answered as ``503`` first;
- prompts containing ``OVERFLOW`` answer miLLM's ``400 context_length_exceeded``;
- ``fail_status``: answer every scoring request with this status (consecutive failures);
- ``swap_model_after`` / ``swap_fingerprint_after``: a response names another model or fingerprint;
- ``foreign_lease``: another holder leases the model (``409 MODEL_LEASED``);
- ``restart()``: every lease ends; renew answers ``404 LEASE_NOT_FOUND``;
- ``judge_answer``: messages → assistant content;
- 009: ``probes`` (``probe_row``), ``engine`` (``llama.cpp`` = GGUF), ``loaded_dtype``,
  ``probe_score``, ``strict_greater``, ``text_input_verified`` and the tokenisation-failure marker
  drive ``POST /api/probes/score`` and ``GET /api/probes[/{id}]`` with miLLM 44e4c4a's bodies and
  refusals (``probe_scoring.py``, ``probe_arming.identity_refusal``, the management envelope).

Every request is recorded with its headers and JSON body, so tests assert payload AND call count.
The context-length body is miLLM's own (``millm/api/routes/openai/errors.py``
``context_length_exceeded_error``, OpenAI's code), not a guessed string.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

JEV_FALSE, JEV_TRUE = 3721, 1802
SAE_FALLBACK = "unclaimed-sae"
ORIGIN = "http://millm.test"
TEI_ORIGIN = "http://tei.test"


def default_p(text: str) -> float:
    digest = hashlib.sha256(text.encode()).digest()
    return 0.02 + 0.96 * (digest[0] / 255.0)


def fake_set_hash(sae_id: str, applied: dict[int, float]) -> str:
    """miLLM 028 FTDD 5.3's set hash, written independently of ``services/generation/steering``
    (a fake computing the hash with the code under test would agree with it by construction)."""
    lines = ["millm.steering-set/v1", f"sae={sae_id}"]
    for index in sorted(applied):
        lines.append(f"{index}:{struct.pack('>d', applied[index]).hex()}")
    return "sha256:" + hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def default_generation(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    """Deterministic generated text: the prompt, the seed and the steering asked for."""
    prompt = str(messages[-1].get("content", "")) if messages else ""
    steer = body.get("steering") or body.get("profile") or "none"
    return f"Reply to [{prompt[:60]}] seed={body.get('seed')} steer={json.dumps(steer, sort_keys=True)}"


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: Any


@dataclass
class FakeMillm:
    resident: dict[str, Any] | None = field(
        default_factory=lambda: {
            "id": 7,
            "name": "JEV-9B-decision",
            "repo_id": "autotrust/JEV-9B-decision",
            "revision": "b63f651ce8ed64481d3f5e73ecdb05f740042f01",
            "quantization": "FP16",
        }
    )
    lease_supported: bool = True
    p_true: dict[str, float] = field(default_factory=dict)
    busy: deque[int | None] = field(default_factory=deque)
    fail_status: int | None = None
    swap_model_after: int | None = None
    swap_fingerprint_after: int | None = None
    foreign_lease: dict[str, Any] | None = None
    judge_answer: Callable[[list[dict[str, Any]]], str] | None = None
    honour_response_format: bool = True
    steering_header: str | None = None
    #: Called with the scoring call count after each successful scoring request.
    on_score: Callable[[int], None] | None = None
    queue: dict[str, Any] = field(
        default_factory=lambda: {
            "queue_pending": 0,
            "in_flight": 0,
            "queue_waiting": 0,
            "batch_backlog_rows": None,
            "estimated_wait_seconds": None,
        }
    )
    requests: list[Recorded] = field(default_factory=list)
    leases: dict[str, dict[str, Any]] = field(default_factory=dict)
    scoring_calls: int = 0
    batches: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    # --- feature 007: profiles, SAE attachments, inline steering, X-miLLM-Steering ------------
    #: profile id -> ProfileResponse-shaped dict (``steering`` at λ=1, ``intensity`` the dial).
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_profile_id: str | None = None
    #: ``[{sae_id, layer}]`` (``GET /api/saes/attachments``).
    attachments: list[dict[str, Any]] = field(default_factory=list)
    #: Whether this miLLM reports steering on chat (False: a pre-028 server, no header). Off by
    #: default so 005's judge tests keep the server they were written against; 007 turns it on.
    reports_steering: bool = False
    #: 007: answer chat requests with generated text (``default_generation``) instead of a verdict.
    generation_mode: bool = False
    #: Per request: ``"real"`` (what the hooks ran) or a scripted defect: ``"missing"``,
    #: ``"unknown"``, ``"changed"``, ``"wrong_hash"``, ``"extra_item"``. Called with the body.
    header_mode: Callable[[dict[str, Any]], str] | None = None
    #: Generated text for a chat request (messages, body) — default echoes prompt, seed, steering.
    gen_answer: Callable[[list[dict[str, Any]], dict[str, Any]], str] | None = None
    #: Called with the chat body BEFORE it is answered (a test edits a profile or swaps a model).
    on_chat: Callable[[dict[str, Any]], None] | None = None
    chat_calls: int = 0
    #: 007's diversity report: the model ``/v1/embeddings`` answers with (swap it to mix spaces).
    embedding_model: str = "embed-model"
    embedding_calls: int = 0
    #: GETs of an in-progress batch before it completes.
    batch_polls_to_complete: int = 1
    #: Called with the batch object on every GET (a test can restart miLLM mid-poll).
    on_batch_poll: Callable[[dict[str, Any]], None] | None = None
    # --- feature 009: imported probes and POST /api/probes/score (miLLM Feature 27) -----------
    #: probe id -> the probe ROW (``probe_row`` builds one): what ``_probe_summary`` serialises.
    probes: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ``transformers`` or ``llama.cpp`` (a GGUF model: no module tree, PROBE_HOOK_UNSUPPORTED).
    engine: str = "transformers"
    #: The loaded model's dtype as miLLM's identity reads it.
    loaded_dtype: str = "bfloat16"
    #: input text -> combined score; default a hash of the text (``default_probe_score``).
    probe_score: Callable[[str], float | None] | None = None
    #: Decide with ``>`` instead of miLLM's ``>=`` (a scripted contract break, P-03).
    strict_greater: bool = False
    #: Whether ``text`` inputs are accepted (miLLM ``TEXT_INPUT_VERIFIED``; False at 44e4c4a).
    text_input_verified: bool = False
    #: Inputs containing this marker fail tokenisation (a per-input ``TOKENIZATION_FAILED``).
    tokenization_fail_marker: str = "UNTOKENIZABLE"
    probe_score_calls: int = 0
    #: ``SAE_ACTIVATIONS_MAX_TOP_K`` (miLLM settings).
    sae_max_top_k: int = 64
    #: A scripted defect: report this read point instead of scoring mode's ``unsteered``.
    sae_read_point: str | None = None
    sae_reads: int = 0

    # --- helpers ------------------------------------------------------------------------------

    def calls(self, path: str, method: str | None = None) -> list[Recorded]:
        return [
            r for r in self.requests if r.path == path and (method is None or r.method == method)
        ]

    def restart(self) -> None:
        self.leases.clear()

    @property
    def fingerprint(self) -> str:
        r = self.resident or {}
        return f"millm:{r.get('name')}@{r.get('revision')}:bfloat16/FP16:transformers"

    @staticmethod
    def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    def _openai_error(self, status: int, code: str, message: str, **headers: str) -> httpx.Response:
        return self._json(
            status,
            {
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": code,
                }
            },
            headers,
        )

    def _mgmt_error(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> httpx.Response:
        return self._json(
            status,
            {
                "success": False,
                "data": None,
                "error": {"code": code, "message": message, "details": details or {}},
            },
        )

    def _lease_view(self, lease: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in lease.items() if k != "lease_id"}

    # --- the transport ------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        # Dispatch at call time, so a test that replaces ``handle`` is really served by it.
        return httpx.MockTransport(lambda request: self.handle(request))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files" and request.method == "POST":
            self.requests.append(Recorded("POST", "/v1/files", dict(request.headers), None))
            return self._upload(request)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append(Recorded(request.method, path, dict(request.headers), body))
        method = request.method
        if path == "/api/health/detailed" and method == "GET":
            health: dict[str, Any] = {
                "status": "ok",
                "model_loaded": self.resident is not None,
                "model_name": f"Model {self.resident['name']} is loaded" if self.resident else None,
                "inference": dict(self.queue),
            }
            if self.lease_supported:
                live = next(iter(self.leases.values()), None)
                health["lease"] = self.foreign_lease or (self._lease_view(live) if live else None)
            return self._json(200, health)
        if path == "/api/models" and method == "GET":
            data = []
            if self.resident:
                data.append({**self.resident, "status": "loaded", "source": "huggingface"})
            data.append(
                {
                    "id": 99,
                    "name": "Qwen2.5-7B",
                    "status": "ready",
                    "repo_id": None,
                    "revision": None,
                    "quantization": "Q4",
                }
            )
            return self._json(200, {"success": True, "data": data})
        if path == "/v1/models" and method == "GET":
            names = [self.resident["name"]] if self.resident else []
            return self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": n, "object": "model"} for n in names + ["Qwen2.5-7B"]],
                },
            )
        if path.startswith("/api/models/") and path.endswith("/lease/renew"):
            return self._renew(request)
        if path.startswith("/api/models/") and path.endswith("/lease"):
            return self._lease(request, body)
        if path.startswith("/v1/batches/") and path.endswith("/lease"):
            return self._batch_lease(request, path)
        if path == "/v1/batches" and method == "POST":
            return self._create_batch(request, body)
        if path.startswith("/v1/batches/") and path.endswith("/cancel"):
            batch = self.batches[path.split("/")[3]]
            batch["status"] = "cancelled"
            return self._json(200, batch)
        if path.startswith("/v1/batches/") and method == "GET":
            return self._get_batch(path.split("/")[3])
        if path.startswith("/v1/files/") and path.endswith("/content"):
            content = self.files.get(path.split("/")[3])
            if content is None:
                return self._openai_error(404, "file_not_found", "no file")
            return httpx.Response(
                200, content=content, headers={"content-type": "application/jsonl"}
            )
        if path.startswith("/api/profiles"):
            return self._profiles(request, path)
        if path == "/api/saes/attachments" and method == "GET":
            return self._json(
                200,
                {
                    "success": True,
                    "data": {
                        "is_attached": bool(self.attachments),
                        "count": len(self.attachments),
                        "entries": [dict(a) for a in self.attachments],
                    },
                },
            )
        if path == "/api/probes/score" and method == "POST":
            return self._probe_score(request, body)
        if path == "/api/probes" and method == "GET":
            return self._json(
                200, {"success": True, "data": [probe_summary(p) for p in self.probes.values()]}
            )
        if path.startswith("/api/probes/") and method == "GET" and path.count("/") == 3:
            row = self.probes.get(path.split("/")[3])
            if row is None:
                return self._mgmt_error(404, "PROBE_NOT_FOUND", f"No probe {path.split('/')[3]}")
            return self._json(
                200,
                {"success": True, "data": {**probe_summary(row), "definition": row["definition"]}},
            )
        if path == "/v1/embeddings" and method == "POST":
            return self._embeddings(body)
        if path == "/v1/completions" and method == "POST":
            return self._completions(request, body)
        if path == "/v1/chat/completions" and method == "POST":
            return self._chat(request, body)
        return self._json(404, {"detail": "Not Found"})

    # --- lease routes ---------------------------------------------------------------------

    def _lease(self, request: httpx.Request, body: Any) -> httpx.Response:
        if not self.lease_supported:
            return self._json(404, {"detail": "Not Found"})
        model_id = int(request.url.path.split("/")[3])
        if request.method == "POST":
            if self.resident is None or self.resident["id"] != model_id:
                return self._mgmt_error(409, "MODEL_NOT_RESIDENT", "not resident")
            if self.foreign_lease or self.leases:
                holder = self.foreign_lease or next(iter(self.leases.values()))
                return self._mgmt_error(
                    409,
                    "MODEL_LEASED",
                    "leased",
                    {"holder": holder["holder"], "expires_at": holder["expires_at"]},
                )
            lease_id = "lease-" + uuid.uuid4().hex
            ttl = int(body.get("ttl_seconds") or 7200)
            lease = {
                "lease_id": lease_id,
                "model_id": model_id,
                "model_name": self.resident["name"],
                "holder": body["holder"],
                "reason": body["reason"],
                "ttl_seconds": ttl,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat(),
            }
            self.leases[lease_id] = lease
            return self._json(201, {"success": True, "data": lease})
        if request.method == "DELETE":
            lease_id = request.headers.get("X-miLLM-Lease", "")
            lease = self.leases.pop(lease_id, None)
            if lease is None:
                return self._mgmt_error(
                    404, "LEASE_NOT_FOUND", "unknown lease; a restart ends every lease"
                )
            return self._json(
                200,
                {"success": True, "data": {**self._lease_view(lease), "end_reason": "released"}},
            )
        return self._json(405, {"detail": "Method Not Allowed"})

    def _renew(self, request: httpx.Request) -> httpx.Response:
        lease_id = request.headers.get("X-miLLM-Lease", "")
        lease = self.leases.get(lease_id)
        if lease is None:
            return self._mgmt_error(
                404, "LEASE_NOT_FOUND", "unknown lease; a restart ends every lease"
            )
        lease["expires_at"] = (
            datetime.now(UTC) + timedelta(seconds=lease["ttl_seconds"])
        ).isoformat()
        return self._json(200, {"success": True, "data": self._lease_view(lease)})

    def _batch_lease(self, request: httpx.Request, path: str) -> httpx.Response:
        batch_id = path.split("/")[3]
        lease_id = request.headers.get("X-miLLM-Lease", "")
        if lease_id not in self.leases:
            return self._openai_error(404, "lease_not_found", "unknown lease")
        batch = self.batches.setdefault(batch_id, {"id": batch_id, "status": "in_progress"})
        if batch["status"] in ("completed", "failed", "cancelled", "expired"):
            return self._openai_error(409, "batch_state_conflict", "terminal")
        batch["lease_id"] = lease_id
        return self._json(200, {**batch, "millm": {"lease_mode": "caller"}})

    # --- batch API (contract v1.11 section 4f) ----------------------------------------------

    def _upload(self, request: httpx.Request) -> httpx.Response:
        raw = request.content
        start = raw.index(b"\r\n\r\n", raw.index(b'filename="')) + 4
        end = raw.index(b"\r\n--", start)
        file_id = "file-" + uuid.uuid4().hex[:12]
        self.files[file_id] = raw[start:end]
        return self._json(
            200, {"id": file_id, "object": "file", "purpose": "batch", "status": "processed"}
        )

    def _create_batch(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        lease_id = request.headers.get("X-miLLM-Lease")
        if self.foreign_lease and lease_id is None:
            return self._openai_error(409, "model_leased", "leased")
        batch_id = "batch_" + uuid.uuid4().hex[:12]
        self.batches[batch_id] = {
            "id": batch_id,
            "object": "batch",
            "endpoint": body["endpoint"],
            "input_file_id": body["input_file_id"],
            "status": "in_progress",
            "output_file_id": None,
            "error_file_id": None,
            "lease_id": lease_id,
            "polls": 0,
            "pack": body.get("pack"),
            "millm": {
                "pack": bool(body.get("pack")),
                "waiting_reason": None,
                "lease_mode": "caller" if lease_id else "own",
            },
        }
        return self._json(200, self._batch_view(self.batches[batch_id]))

    @staticmethod
    def _batch_view(batch: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in batch.items() if k not in ("lease_id", "polls")}

    def _get_batch(self, batch_id: str) -> httpx.Response:
        batch = self.batches.get(batch_id)
        if batch is None:
            return self._openai_error(404, "batch_not_found", "no batch")
        if self.on_batch_poll is not None:
            self.on_batch_poll(batch)
        if batch["status"] == "in_progress":
            if batch["lease_id"] is not None and batch["lease_id"] not in self.leases:
                batch["millm"]["waiting_reason"] = "lease_unavailable"
                return self._json(200, self._batch_view(batch))
            batch["millm"]["waiting_reason"] = None
            batch["polls"] += 1
            if batch["polls"] >= self.batch_polls_to_complete:
                self._finish_batch(batch)
        return self._json(200, self._batch_view(batch))

    def _finish_batch(self, batch: dict[str, Any]) -> None:
        out, errors = [], []
        for raw in self.files[batch["input_file_id"]].decode().splitlines():
            line = json.loads(raw)
            prompt = str(line["body"].get("prompt", ""))
            if "OVERFLOW" in prompt:
                errors.append(
                    {
                        "id": "e",
                        "custom_id": line["custom_id"],
                        "response": {
                            "status_code": 400,
                            "body": {
                                "error": {"code": "context_length_exceeded", "message": "too long"}
                            },
                        },
                        "error": None,
                    }
                )
                continue
            self.scoring_calls += 1
            p = self._p_for(prompt)
            top = {f"token_id:{JEV_FALSE}": math.log(1 - p), f"token_id:{JEV_TRUE}": math.log(p)}
            body = {
                "model": self._response_model(),
                "system_fingerprint": self._response_fingerprint(),
                "choices": [{"index": 0, "text": "true", "logprobs": {"top_logprobs": [top]}}],
                "usage": {"prompt_tokens": max(1, len(prompt) // 4)},
            }
            out.append(
                {
                    "id": "o",
                    "custom_id": line["custom_id"],
                    "response": {
                        "status_code": 200,
                        "request_id": "r",
                        "body": body,
                        "millm": {"packed": False, "headers": {}},
                    },
                    "error": None,
                }
            )
        out_id, err_id = "file-out-" + batch["id"], "file-err-" + batch["id"]
        self.files[out_id] = "".join(json.dumps(x) + "\n" for x in out).encode()
        self.files[err_id] = "".join(json.dumps(x) + "\n" for x in errors).encode()
        batch.update(
            status="completed", output_file_id=out_id, error_file_id=err_id if errors else None
        )

    # --- inference ------------------------------------------------------------------------

    def _gate(self, request: httpx.Request, model: str | None) -> httpx.Response | None:
        if self.busy:
            retry_after = self.busy.popleft()
            headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
            return self._openai_error(503, "queue_full", "The request queue is full", **headers)
        if self.foreign_lease and request.headers.get("X-miLLM-Lease") is None:
            return self._openai_error(409, "model_leased", "another holder leases the model")
        if self.resident is None or model != self.resident["name"]:
            if request.headers.get("X-miLLM-Load-Policy", "").lower() == "refuse":
                return self._openai_error(409, "model_not_resident", f"{model} is not resident")
            return self._openai_error(500, "would_load", "the fake refuses to load a model")
        if self.fail_status is not None:
            return self._openai_error(self.fail_status, "server_error", "scripted failure")
        return None

    def _response_model(self) -> str:
        assert self.resident is not None
        if self.swap_model_after is not None and self.scoring_calls > self.swap_model_after:
            return "Qwen2.5-7B"
        return str(self.resident["name"])

    def _response_fingerprint(self) -> str:
        if (
            self.swap_fingerprint_after is not None
            and self.scoring_calls > self.swap_fingerprint_after
        ):
            return self.fingerprint.replace("bfloat16", "float16")
        return self.fingerprint

    def _p_for(self, text: str) -> float:
        for key, value in self.p_true.items():
            if key in text:
                return value
        return default_p(text)

    def _completions(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        gated = self._gate(request, body.get("model"))
        if gated is not None:
            return gated
        prompt = str(body.get("prompt", ""))
        spec = body.get("return_sae_activations")
        if spec is not None:
            refused = self._refuse_activations(spec)
            if refused is not None:
                return refused
        if "OVERFLOW" in prompt:
            return self._openai_error(
                400, "context_length_exceeded", "This model's maximum context length is 8192 tokens"
            )
        if spec is not None and not (body.get("logprobs") or body.get("allowed_token_ids")):
            return self._openai_error(500, "fake_generation", "the fake generates nothing")
        if spec is not None:
            self.sae_reads += 1
            return self._json(
                200,
                {
                    "id": "cmpl-sae",
                    "object": "text_completion",
                    "model": self._response_model(),
                    "choices": [{"index": 0, "text": "x", "logprobs": {"top_logprobs": [{}]}}],
                    "usage": {"prompt_tokens": len(prompt), "completion_tokens": 1},
                    "millm": {"sae_activations": self._activations(spec, prompt)},
                },
            )
        self.scoring_calls += 1
        if self.on_score is not None:
            self.on_score(self.scoring_calls)
        p = self._p_for(prompt)
        top = {f"token_id:{JEV_FALSE}": math.log(1 - p), f"token_id:{JEV_TRUE}": math.log(p)}
        headers = {"X-miLLM-Steering": self.steering_header} if self.steering_header else {}
        return self._json(
            200,
            {
                "id": "cmpl-1",
                "object": "text_completion",
                "model": self._response_model(),
                "system_fingerprint": self._response_fingerprint(),
                "choices": [
                    {
                        "index": 0,
                        "text": "true",
                        "logprobs": {"top_logprobs": [top], "tokens": ["token_id:1802"]},
                    }
                ],
                "usage": {"prompt_tokens": max(1, len(prompt) // 4), "completion_tokens": 1},
            },
            headers,
        )

    def _chat(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        gated = self._gate(request, body.get("model"))
        if gated is not None:
            return gated
        messages = body.get("messages") or []
        text = " ".join(str(m.get("content", "")) for m in messages)
        if "OVERFLOW" in text:
            return self._openai_error(400, "context_length_exceeded", "maximum context length")
        if body.get("response_format") is not None and not self.honour_response_format:
            return self._openai_error(400, "response_format_unsupported", "GGUF model")
        self.scoring_calls += 1
        headers: dict[str, str] = {}
        if body.get("seed") is not None:
            headers["X-miLLM-Seed"] = f'{body["seed"]};scope="request"'
        if body.get("logprobs") is True:
            p = self._p_for(text)
            content = [
                {
                    "token": "token_id:1802",
                    "logprob": math.log(p),
                    "top_logprobs": [
                        {"token": f"token_id:{JEV_FALSE}", "logprob": math.log(1 - p)},
                        {"token": f"token_id:{JEV_TRUE}", "logprob": math.log(p)},
                    ],
                }
            ]
            choice: dict[str, Any] = {
                "index": 0,
                "message": {"role": "assistant", "content": "true"},
                "logprobs": {"content": content},
            }
        else:
            self.chat_calls += 1
            if self.on_chat is not None:
                self.on_chat(body)
            if self.gen_answer is not None:
                answer = self.gen_answer(messages, body)
            elif self.judge_answer is not None:
                answer = self.judge_answer(messages)
            elif self.generation_mode:
                answer = default_generation(messages, body)
            else:
                answer = "Looks fine.\nVERDICT: yes"
            choice = {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
            steering_value = self._steering_header(body)
            if steering_value is not None:
                headers["X-miLLM-Steering"] = steering_value
        return self._json(
            200,
            {
                "id": "chat-1",
                "object": "chat.completion",
                "model": self._response_model(),
                "system_fingerprint": self._response_fingerprint(),
                "choices": [choice],
                "usage": {"prompt_tokens": max(1, len(text) // 4)},
            },
            headers,
        )

    # --- feature 009: stateless probe scoring (miLLM probe_scoring.py at 44e4c4a) -------------

    def _probe_score(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        """``ProbeScoringService.score``'s refusals in its order, then one result per input."""
        if self.busy:
            retry_after = self.busy.popleft()
            headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
            return self._json(
                503,
                {
                    "success": False,
                    "data": None,
                    "error": {"code": "QUEUE_FULL", "message": "busy", "details": {}},
                },
                headers,
            )
        if set(body) - {"probe_ids", "inputs", "windows", "return_token_ids"}:
            return self._json(422, {"detail": "extra fields not permitted"})  # extra="forbid"
        inputs = body.get("inputs") or []
        if not inputs:
            return self._mgmt_error(
                400, "INVALID_PROBE_SCORE_REQUEST", "A scoring request needs at least one input"
            )
        for i, item in enumerate(inputs):
            kinds = [k for k in ("token_ids", "messages", "text") if item.get(k) is not None]
            if len(kinds) != 1:
                return self._mgmt_error(
                    400,
                    "INVALID_PROBE_SCORE_REQUEST",
                    f"input {i} must be exactly one of token_ids, messages or text",
                )
            if item.get("text") is not None and not self.text_input_verified:
                return self._mgmt_error(
                    400,
                    "INVALID_PROBE_SCORE_REQUEST",
                    "`text` inputs are not enabled yet: the one-user-turn render (T-49) has not "
                    "yet reproduced a miStudio-reported AUROC on this probe's model.",
                    {"pending_verification": "T-49 (027 FTASKS 0.2)"},
                )
        if self.resident is None:
            return self._mgmt_error(
                409,
                "PROBE_NO_MODEL_LOADED",
                "No model is loaded, so there is nothing to check this probe against",
            )
        if self.engine != "transformers":
            return self._mgmt_error(
                409,
                "PROBE_HOOK_UNSUPPORTED",
                f"This model is served by {self.engine!r}, which exposes no module tree to hook; "
                "probes need a transformers model",
                {"engine": self.engine},
            )
        rows = []
        for probe_id in dict.fromkeys(body.get("probe_ids") or list(self.probes)):
            row = self.probes.get(probe_id)
            if row is None:
                return self._mgmt_error(404, "PROBE_NOT_FOUND", f"No probe {probe_id}",
                                        {"probe_id": probe_id})  # fmt: skip
            model = row["definition"]["model"]
            mismatches = []
            if model["hf_id"] != self.resident.get("repo_id"):
                mismatches.append(
                    {"field": "hf_id", "expected": model["hf_id"],
                     "actual": self.resident.get("repo_id")}
                )  # fmt: skip
            if model.get("load_dtype") and model["load_dtype"] != self.loaded_dtype:
                mismatches.append(
                    {"field": "load_dtype", "expected": model["load_dtype"],
                     "actual": self.loaded_dtype}
                )  # fmt: skip
            if mismatches and {m["field"] for m in mismatches} <= {"load_dtype"}:
                return self._mgmt_error(
                    409,
                    "PROBE_DTYPE_MISMATCH",
                    "This probe reads the right model at the wrong precision (load_dtype: fitted "
                    f"at {model['load_dtype']}, loaded at {self.loaded_dtype}).",
                    {"mismatches": mismatches},
                )
            if mismatches:
                return self._mgmt_error(
                    409,
                    "PROBE_MODEL_MISMATCH",
                    "This probe was fitted on a different model than the one loaded",
                    {"mismatches": mismatches},
                )
            rows.append(row)
        windows = body.get("windows")
        results = []
        for i, item in enumerate(inputs):
            messages = item.get("messages") or [{"role": "user", "content": item.get("text")}]
            text = " ".join(str(m["content"]) for m in messages)
            rendered = "".join(f"<|{m['role']}|>{m['content']}" for m in messages) + "<|assistant|>"
            ids = [ord(c) % 1000 for c in rendered]
            entry: dict[str, Any] = {
                "index": i,
                "input_kind": "messages" if item.get("messages") else "text",
                "n_tokens": len(ids),
                "prompt_tokens": len(ids),
                "token_ids": ids if body.get("return_token_ids") else None,
                "verdicts": [],
                "error": None,
            }
            if self.tokenization_fail_marker in text:
                entry.update(
                    n_tokens=0, prompt_tokens=None, token_ids=None,
                    error={"code": "TOKENIZATION_FAILED", "message": "rendering failed: fake"},
                )  # fmt: skip
                results.append(entry)
                continue
            self.probe_score_calls += 1
            score = self.probe_score(text) if self.probe_score else default_probe_score(text)
            for row in rows:
                for window in windows or ["all"]:
                    entry["verdicts"].append(self._verdict(row, window, score, len(ids)))
            results.append(entry)
        return self._json(
            200,
            {
                "success": True,
                "data": {
                    "model": {
                        "hf_id": self.resident.get("repo_id"),
                        "revision": self.resident.get("revision"),
                        "dtype": self.loaded_dtype,
                        "quantization": self.resident.get("quantization"),
                    },
                    "probes": [
                        {"probe_id": r["id"], "name": r["name"], "layer": r["layer"],
                         "parity": {"status": "never_run", "checked_against": None,
                                    "checked_at": None}}
                        for r in rows
                    ],  # fmt: skip
                    "skipped": [],
                    "results": results,
                },
            },
        )

    def _verdict(
        self, row: dict[str, Any], window: str, score: float | None, n_tokens: int = 12
    ) -> dict[str, Any]:
        """``verdict_payload`` of one ``Verdict`` (``_verdict_for``: ``score >= threshold``).

        A window whose definition records ``length_bands`` is judged at the band holding the
        input's token count (miLLM ``40bbbd2``: a band refines the window's own bar), so the
        verdict's ``threshold`` is the band's, not the window's."""
        bars = window_thresholds(row)
        own = bars.get(window)
        threshold = own if own is not None else row["threshold"]
        spec = (row["definition"]["decision"].get("windows") or {}).get(window) or {}
        for band in spec.get("length_bands") or []:
            hi = band.get("max_tokens")
            if int(band.get("min_tokens") or 0) <= n_tokens and (hi is None or n_tokens <= hi):
                threshold = band["threshold"]
                break
        provisional = own is None or window == "response"
        if score is None:
            fires, reason, n = None, "no_scored_tokens", 0
        elif threshold is None:
            fires, reason, n = None, None, 12
        else:
            fires = score > threshold if self.strict_greater else score >= threshold
            reason, n = None, n_tokens
        return {
            "probe_id": row["id"],
            "name": row["name"],
            "window": window,
            "score": score,
            "threshold": threshold,
            "verdict": fires,
            "rung": row["rung"],
            "rung_language": row["rung_language"],
            "provisional": provisional,
            "threshold_revision": row["threshold_revision"],
            "n_scored_tokens": n,
            "not_scored_reason": reason,
        }

    # --- feature 009: return_sae_activations in scoring mode (request_activations.py) ---------

    def _refuse_activations(self, spec: dict[str, Any]) -> httpx.Response | None:
        """``refuse_before_generation``'s order: engine, top_k cap, then the SAE selection."""
        if self.engine != "transformers":
            return self._openai_error(
                400,
                "engine_unsupported",
                "Per-request SAE activations need the transformers engine; llama.cpp exposes no "
                "layer to read.",
            )
        if int(spec["top_k"]) > self.sae_max_top_k:
            return self._openai_error(
                400,
                "sae_activations_refused",
                f"top_k {spec['top_k']} exceeds the limit of {self.sae_max_top_k}",
            )
        sae_id = spec.get("sae_id")
        names = [a["sae_id"] for a in self.attachments]
        if sae_id is not None and sae_id not in names:
            return self._openai_error(
                400,
                "sae_not_attached",
                f"SAE {sae_id!r} is not attached, so its activations cannot be returned; attach "
                "it first",
            )
        if sae_id is None and len(names) != 1:
            return self._openai_error(
                400,
                "sae_not_attached" if not names else "sae_activations_refused",
                "No SAE is attached" if not names else "Several SAEs are attached; name one",
            )
        return None

    def _activations(self, spec: dict[str, Any], prompt: str) -> dict[str, Any]:
        sae_id = spec.get("sae_id") or self.attachments[0]["sae_id"]
        layer = next(a["layer"] for a in self.attachments if a["sae_id"] == sae_id)
        n = max(1, len(prompt))
        positions = [n - 1] if spec["positions"] == "last" else list(range(min(n, 8)))
        candidates = spec.get("features") or list(range(256))
        out = []
        for position in positions:
            scored = []
            for index in candidates:
                digest = hashlib.sha256(f"{prompt}|{position}|{index}".encode()).digest()
                scored.append((round(digest[0] / 25.5, 3), int(index)))
            scored.sort(key=lambda t: (-t[0], t[1]))
            out.append(
                {
                    "position": position,
                    "token_id": ord(prompt[position]) % 1000 if prompt else None,
                    "features": [{"index": i, "value": v} for v, i in scored[: int(spec["top_k"])]],
                }
            )
        read_point = self.sae_read_point or "unsteered"
        return {
            "sae_id": sae_id,
            "layer": layer,
            "read_point": read_point,
            "positions": out,
            "note": f"{read_point} is what the model computed at this layer for this request",
        }

    # --- feature 007: profiles and the steering report ---------------------------------------

    def _embeddings(self, body: dict[str, Any]) -> httpx.Response:
        self.embedding_calls += 1
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = []
        for i, text in enumerate(items):
            words = str(text).lower().split()
            vector = [0.0] * 16
            for word in words:  # a bag of hashed words: similar texts get similar vectors
                vector[hashlib.sha256(word.encode()).digest()[0] % 16] += 1.0
            data.append({"object": "embedding", "index": i, "embedding": vector})
        return self._json(
            200, {"object": "list", "model": self.embedding_model, "data": data, "usage": {}}
        )

    def _profiles(self, request: httpx.Request, path: str) -> httpx.Response:
        if request.method != "GET":
            # miDataworks must never write miLLM state (007 FR-007.18); the fake records and refuses.
            return self._mgmt_error(405, "READ_ONLY_FAKE", "007 never writes profiles")
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2:
            return self._json(
                200,
                {
                    "success": True,
                    "data": {
                        "profiles": [dict(p) for p in self.profiles.values()],
                        "total": len(self.profiles),
                        "active_profile_id": self.active_profile_id,
                    },
                },
            )
        profile = self.profiles.get(parts[2])
        if profile is None:
            return self._mgmt_error(404, "PROFILE_NOT_FOUND", "no profile")
        return self._json(200, {"success": True, "data": dict(profile)})

    def _applied(self, body: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """What the hooks run for this request: (kind, details) — miLLM 028's labelling order."""
        steering = body.get("steering")
        if isinstance(steering, dict):
            features = steering.get("features") or []
            if not features:
                return None  # explicit unsteered: every attached SAE is off for this request
            sae = steering.get("sae_id") or (
                self.attachments[0]["sae_id"] if self.attachments else ""
            )
            pairs = [(int(f["index"]), float(f["strength"])) for f in features]
            return "inline", {"sae": sae, "pairs": pairs}
        name = body.get("profile")
        if name is not None:
            profile = next((p for p in self.profiles.values() if p["name"] == name), None)
            if profile is None:
                return None
            lam = float(profile.get("intensity", 1.0))
            pairs = [(int(k), float(v) * lam) for k, v in profile["steering"].items()]
            return "profile", {"profile": profile, "pairs": pairs, "source": "request", "lam": lam}
        if self.active_profile_id is not None:
            profile = self.profiles[self.active_profile_id]
            lam = float(profile.get("intensity", 1.0))
            pairs = [(int(k), float(v) * lam) for k, v in profile["steering"].items()]
            return "profile", {"profile": profile, "pairs": pairs, "source": "active", "lam": lam}
        return None

    def _steering_header(self, body: dict[str, Any]) -> str | None:
        if not self.reports_steering:
            return None
        mode = self.header_mode(body) if self.header_mode is not None else "real"
        if mode == "missing":
            return None
        if mode == "unknown":
            return "unknown;reason=read_failed"
        applied = self._applied(body)
        if applied is None and mode == "wrong_hash":
            # the hooks ran something nobody asked for (an operator write mid-request)
            value = f'manual;sae="{SAE_FALLBACK}";layer=0;features=1;hash="sha256:{"0" * 64}"'
        elif applied is None:
            value = "none"
        else:
            kind, info = applied
            sae = info["profile"]["sae_id"] if kind == "profile" else info["sae"]
            layer = next((a["layer"] for a in self.attachments if a["sae_id"] == sae), 0)
            clamped = sum(1 for _, s in info["pairs"] if abs(s) > 200.0)
            kept = {i: max(-200.0, min(200.0, s)) for i, s in info["pairs"]}
            kept = {i: s for i, s in kept.items() if s != 0.0}
            digest = fake_set_hash(sae, kept)
            if mode == "wrong_hash":
                digest = "sha256:" + "0" * 64
            params = f'sae="{sae}";layer={layer};features={len(kept)};hash="{digest}"'
            if kind == "profile":
                name = info["profile"]["name"].replace("%", "%25").replace('"', "%22")
                value = (
                    f'profile;name="{name}";source={info["source"]};'
                    f'intensity="{repr(float(info["lam"]))}";{params}'
                )
            else:
                value = f"inline;{params}"
            if clamped:
                value += f";clamped={clamped}"
        if mode == "changed":
            value += ";changed"
        if mode == "extra_item":
            value += ', manual;sae="x";layer=3;features=1;hash="sha256:' + "1" * 64 + '"'
        return value


def default_probe_score(text: str) -> float:
    """A deterministic combined score for a text, spread over [-10, 10]."""
    digest = hashlib.sha256(("probe:" + text).encode()).digest()
    return -10.0 + 20.0 * (digest[1] / 255.0)


def probe_row(
    probe_id: str = "pr_humor",
    *,
    hf_id: str = "autotrust/JEV-9B-decision",
    threshold: float | None = 2.5,
    windows: dict[str, float | None] | None = None,
    threshold_revision: int = 1,
    mistudio_probe_id: str | None = "pm_humor",
    load_dtype: str | None = "bfloat16",
    rung: int = 2,
    length_bands: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """A miLLM probe row as ``probes`` stores it: the definition carries the model block, the
    per-window bars (``decision.windows``) and the miStudio provenance."""
    windows = {"all": threshold} if windows is None else windows
    provenance: dict[str, Any] = {"run_id": "pmr_1"}
    if mistudio_probe_id is not None:
        provenance["probe_id"] = mistudio_probe_id
    return {
        "id": probe_id,
        "name": f"{probe_id} probe",
        "hf_id": hf_id,
        "layer": 16,
        "rule": "mean",
        "scope": "all",
        "basis": "dense",
        "streamable": True,
        "threshold": threshold,
        "target_fpr": 0.01,
        "threshold_revision": threshold_revision,
        "rung": rung,
        "rung_language": "detects on unseen tasks" if rung >= 2 else "fitted, not yet tested",
        "armed": False,
        "created_at": "2026-10-07T00:00:00Z",
        "definition": {
            "model": {"hf_id": hf_id, "load_dtype": load_dtype, "d_model": 4096, "n_layers": 32},
            "decision": {"threshold": threshold, "windows": {
                w: {"threshold": t, **(
                    {"length_bands": length_bands[w]} if length_bands and w in length_bands else {}
                )} for w, t in windows.items()
            }},  # fmt: skip
            "aggregation": {"rule": "mean", "params": {}},
            "provenance": provenance,
        },
    }


def window_thresholds(row: dict[str, Any]) -> dict[str, float]:
    """miLLM ``window_thresholds_from_definition``: windows that placed a bar (null = none)."""
    out: dict[str, float] = {}
    for name, entry in (row["definition"]["decision"].get("windows") or {}).items():
        value = entry.get("threshold") if isinstance(entry, dict) else None
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[name] = float(value)
    return out


def probe_summary(row: dict[str, Any]) -> dict[str, Any]:
    """miLLM ``_probe_summary`` (``millm/api/routes/management/probes.py:110``) of a row."""
    definition = row["definition"]
    return {
        "id": row["id"],
        "name": row["name"],
        "hf_id": row["hf_id"],
        "layer": row["layer"],
        "rule": row["rule"],
        "rule_params": (definition.get("aggregation") or {}).get("params") or {},
        "window_thresholds": window_thresholds(row),
        "length_band_count": 0,
        "scope": row["scope"],
        "basis": row["basis"],
        "streamable": row["streamable"],
        "threshold": row["threshold"],
        "target_fpr": row["target_fpr"],
        "threshold_revision": row["threshold_revision"],
        "rung": row["rung"],
        "rung_language": row["rung_language"],
        "next_step": "test it on your own traffic",
        "concept": None,
        "label_mapping": None,
        "load_dtype": (definition.get("model") or {}).get("load_dtype"),
        "armed": row["armed"],
        "paused_reason": None,
        "parity": None,
        "created_at": row["created_at"],
    }


@dataclass
class FakeTEI:
    model_id: str = "protectai/deberta-v3-base-prompt-injection-v2"
    model_sha: str | None = "e6535ca4ce3ba852083e75ec585d7c8aeb4be4c5"
    labels: tuple[str, str] = ("SAFE", "INJECTION")
    max_tokens: int = 512
    requests: list[Recorded] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        # Dispatch at call time, so a test that replaces ``handle`` is really served by it.
        return httpx.MockTransport(lambda request: self.handle(request))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files" and request.method == "POST":
            self.requests.append(Recorded("POST", "/v1/files", dict(request.headers), None))
            return self._upload(request)
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append(Recorded(request.method, path, dict(request.headers), body))
        if path == "/info":
            return httpx.Response(
                200,
                json={
                    "model_id": self.model_id,
                    "model_sha": self.model_sha,
                    "model_type": {
                        "classifier": {"id2label": {"0": self.labels[0], "1": self.labels[1]}}
                    },
                },
            )
        if path == "/predict":
            text = str(body.get("inputs"))
            if "OVERFLOW" in text:
                # Shaped from TEI's documented validation refusal under truncate=false. UNVERIFIED
                # against a live TEI (005 FTASKS 1.6).
                return httpx.Response(
                    413,
                    json={
                        "error": f"Input validation error: `inputs` must have less than {self.max_tokens} tokens. Given: 900",
                        "error_type": "Validation",
                    },
                )
            p = default_p(text)
            return httpx.Response(
                200,
                json=[
                    {"label": self.labels[1], "score": p},
                    {"label": self.labels[0], "score": 1 - p},
                ],
            )
        if path.startswith("/api/") or path == "/v1/models":
            return httpx.Response(
                404, text="<html>Not Found</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(404, json={"error": "Not Found"})


def route(servers: dict[str, Any]) -> Callable[[str], httpx.BaseTransport]:
    """A transport factory for ``endpoint_caller.install_transport``: origin → fake."""
    table = {origin: server.transport() for origin, server in servers.items()}

    def factory(origin: str) -> httpx.BaseTransport:
        if origin in table:
            return table[origin]

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no fake for " + origin, request=request)

        return httpx.MockTransport(refuse)

    return factory
