"""Server kind, resident model, lease support and the capability check (FR-005.9; FTID 005 7.2).

- ``GET {origin}/api/health/detailed`` with an ``inference`` object → **miLLM**. A ``lease`` key
  present → the lease surface is served (miLLM FR-29.5.1). Queue depth comes from
  ``inference``: ``queue_pending``, ``in_flight``, ``queue_waiting``, ``batch_backlog_rows``,
  ``estimated_wait_seconds`` (miLLM contract v1.11 section 3) — each copied as served, a ``null``
  kept ``null`` ("unmeasured", never 0).
- The **resident model** is the item of ``GET {origin}/api/models`` (``{success, data}``) whose
  ``status`` is ``loaded``: its integer ``id`` is the ``{model_id}`` of the lease routes, and its
  ``name``, ``repo_id``, ``revision`` and ``quantization`` identify it (miLLM
  ``millm/api/routes/management/models.py``; ``ModelStatus.LOADED``). NOT from
  ``/api/health/detailed``, which names the model only inside a message string (Stage 3,
  2026-10-06).
- Else ``GET {origin}/info`` with ``model_id`` → **TEI**.
- Else an **OpenAI-compatible** server.

Probe failures here never decide a kind by guessing: an unreachable server raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..clients.endpoint_caller import EndpointCaller
from ..clients.endpoint_errors import (
    EndpointCallError,
    EndpointNotJson,
    EndpointUnauthorized,
    EndpointUnreachable,
    ModelNotResident,
    ProtocolUnsupported,
    TransientError,
)
from ..schemas.labeling import EndpointTestResult
from .endpoint_resolver import ResolvedRoleEndpoint

ServerKind = Literal["millm", "tei", "openai_compatible"]

QUEUE_FIELDS: tuple[str, ...] = (
    "queue_pending",
    "in_flight",
    "queue_waiting",
    "batch_backlog_rows",
    "estimated_wait_seconds",
)


@dataclass(frozen=True)
class ResidentModel:
    id: int
    name: str
    repo_id: str | None
    revision: str | None
    quantization: str | None


@dataclass(frozen=True)
class ServerInfo:
    kind: ServerKind
    resident: ResidentModel | None = None
    lease_supported: bool = False
    #: miLLM's ``lease`` object (no ``lease_id`` is ever served there), or None.
    lease: dict[str, Any] | None = None
    queue: dict[str, Any] | None = None
    tei_model_id: str | None = None
    tei_model_sha: str | None = None
    raw_health: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_millm(self) -> bool:
        return self.kind == "millm"


def queue_depth(health: dict[str, Any]) -> dict[str, Any] | None:
    """The queue fields of ``inference`` as served (``null`` stays ``null``)."""
    inference = health.get("inference")
    if not isinstance(inference, dict):
        return None
    return {name: inference.get(name) for name in QUEUE_FIELDS}


def resident_model(models_body: Any) -> ResidentModel | None:
    """The one item of ``GET /api/models`` whose status is ``loaded``; None when none is."""
    data = models_body.get("data") if isinstance(models_body, dict) else None
    if not isinstance(data, list):
        raise ProtocolUnsupported("miLLM's /api/models did not return {data: [...]}")
    for item in data:
        if isinstance(item, dict) and item.get("status") == "loaded":
            model_id = item.get("id")
            name = item.get("name")
            if not isinstance(model_id, int) or not isinstance(name, str):
                raise ProtocolUnsupported("the loaded model has no integer id or name")
            quantization = item.get("quantization")
            return ResidentModel(
                model_id,
                name,
                item.get("repo_id") if isinstance(item.get("repo_id"), str) else None,
                item.get("revision") if isinstance(item.get("revision"), str) else None,
                str(quantization) if quantization is not None else None,
            )
    return None


def detect_server(caller: EndpointCaller) -> ServerInfo:
    """Which server is behind ``caller``. Raises :class:`EndpointUnreachable` when none answers."""
    health: dict[str, Any] | None = None
    try:
        response = caller.raw("GET", "/api/health/detailed")
        if response.status == 200 and isinstance(response.body, dict):
            health = response.body
    except EndpointNotJson:
        health = None
    except TransientError:
        health = None
    if health is not None and isinstance(health.get("inference"), dict):
        models = caller.raw("GET", "/api/models")
        if models.status != 200:
            raise ProtocolUnsupported(f"miLLM's /api/models answered {models.status}")
        lease = health.get("lease")
        return ServerInfo(
            "millm",
            resident=resident_model(models.body),
            lease_supported="lease" in health,
            lease=lease if isinstance(lease, dict) else None,
            queue=queue_depth(health),
            raw_health=health,
        )
    try:
        info = caller.raw("GET", "/info")
        if info.status == 200 and isinstance(info.body, dict):
            model_id = info.body.get("model_id")
            if isinstance(model_id, str) and model_id:
                sha = info.body.get("model_sha")
                return ServerInfo(
                    "tei",
                    tei_model_id=model_id,
                    tei_model_sha=sha if isinstance(sha, str) else None,
                )
    except (EndpointNotJson, TransientError):
        pass
    return ServerInfo("openai_compatible")


def model_revision(info: ServerInfo, model_id: str) -> str | None:
    """The revision to put in the labeler identity, or None ("revision not reported", P-13).

    miLLM: the resident row's ``revision`` when the resident model is the run's. TEI: ``/info``
    ``model_sha``. Anything else: not reported."""
    if info.kind == "millm" and info.resident is not None and info.resident.name == model_id:
        return info.resident.revision
    if info.kind == "tei":
        return info.tei_model_sha
    return None


def _minimal_request(caller: EndpointCaller, protocol: str, model: str) -> None:
    """One minimal request in the role's protocol (FR-005.9). Raises on failure."""
    if protocol == "tei_classification":
        caller.call("POST", "/predict", body={"inputs": "Test"}, purpose="scoring", openai=False)
        return
    if protocol == "openai_scoring":
        response = caller.call(
            "POST",
            "/v1/completions",
            body={
                "model": model,
                "prompt": "Test",
                "max_tokens": 1,
                "temperature": 1.0,
                "logprobs": 1,
                "allowed_token_ids": [0],
            },
            purpose="scoring",
            openai=True,
        )
        choices = response.body.get("choices") if isinstance(response.body, dict) else None
        if not choices or not isinstance(choices[0], dict) or not choices[0].get("logprobs"):
            raise ProtocolUnsupported(
                "The server answered without logprobs: it does not serve scoring mode."
            )
        return
    if protocol in ("openai_chat",):
        caller.call(
            "POST",
            "/v1/chat/completions",
            body={
                "model": model,
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_tokens": 1,
                "temperature": 0.0,
            },
            purpose="judge",
            openai=True,
        )
        return
    if protocol in ("openai_embeddings",):
        caller.call(
            "POST",
            "/v1/embeddings",
            body={"model": model, "input": "Test"},
            purpose="probe",
            openai=True,
        )
        return
    if protocol == "tei_embeddings":
        caller.call("POST", "/embed", body={"inputs": "Test"}, purpose="probe", openai=False)
        return
    raise ProtocolUnsupported(f"No capability check is defined for {protocol}.")


def _listed(caller: EndpointCaller, info: ServerInfo, model: str) -> bool | None:
    if info.kind == "tei":
        return info.tei_model_id == model
    response = caller.raw("GET", "/v1/models", openai=True)
    if response.status in (401, 403):
        raise EndpointUnauthorized(f"{caller.origin} refused the API key ({response.status}).")
    if response.status != 200 or not isinstance(response.body, dict):
        return None
    ids = [d.get("id") for d in response.body.get("data") or [] if isinstance(d, dict)]
    return model in ids


def check(resolved: ResolvedRoleEndpoint, caller: EndpointCaller) -> EndpointTestResult:
    """The "Test endpoint" result for a role: reachable, listed, protocol, miLLM and lease."""
    try:
        info = detect_server(caller)
        listed = _listed(caller, info, resolved.model_id)
    except EndpointCallError as exc:
        return EndpointTestResult(
            role=resolved.role,
            reachable=not isinstance(exc, EndpointUnreachable),
            model_listed=None,
            protocol_ok=None,
            server_kind=None,
            resident_model=None,
            lease_supported=None,
            lease_state=None,
            queue=None,
            error_code=exc.code,
            message=exc.message,
        )
    resident = info.resident.name if info.resident is not None else None
    lease_state = None
    if info.is_millm:
        lease_state = (
            "not served"
            if not info.lease_supported
            else (
                f"held by {info.lease.get('holder')} until {info.lease.get('expires_at')}"
                if info.lease
                else "free"
            )
        )
    common: dict[str, Any] = {
        "role": resolved.role,
        "reachable": True,
        "model_listed": listed,
        "server_kind": info.kind,
        "resident_model": resident,
        "lease_supported": info.lease_supported if info.is_millm else False,
        "lease_state": lease_state,
        "queue": info.queue,
    }
    if info.is_millm and resident != resolved.model_id:
        return EndpointTestResult(
            **common,
            protocol_ok=None,
            error_code="MODEL_NOT_LOADED",
            message=(
                f"{resolved.model_id} is not loaded in miLLM ({resident or 'nothing'} is). "
                f"Load {resolved.model_id} in miLLM, then test again."
            ),
        )
    try:
        _minimal_request(caller, resolved.protocol, resolved.model_id)
    except ModelNotResident as exc:
        return EndpointTestResult(
            **common, protocol_ok=None, error_code="MODEL_NOT_LOADED", message=exc.message
        )
    except EndpointCallError as exc:
        code = (
            exc.code
            if exc.code.startswith(("ENDPOINT_", "PROTOCOL_"))
            else ("PROTOCOL_UNSUPPORTED")
        )
        return EndpointTestResult(**common, protocol_ok=False, error_code=code, message=exc.message)
    return EndpointTestResult(
        **common,
        protocol_ok=True,
        error_code=None,
        message=f"{resolved.model_id} answers on {resolved.base_url} ({info.kind}).",
    )
