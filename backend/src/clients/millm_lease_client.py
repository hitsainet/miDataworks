"""miLLM's model-lease routes and the batch re-attach route (miLLM contract v1.11 sections 4e, 4f).

Foundation task 7.3 named this client and was blocked until miLLM served the lease (BRD-04
R-04.38). miLLM now serves it (contract v1.9+; ADR-027 check recorded in 005 FTASKS 1.2), so feature
005 builds it here. ``services/model_lease_holder.py`` is its ONLY caller (an AST test enforces
it): two features building their own lease logic is the risk the shared holder exists to remove.

| Route                                   | Success                       | Mapped refusals                           |
|-----------------------------------------|-------------------------------|-------------------------------------------|
| ``POST /api/models/{id}/lease``         | 201, the grant with lease_id  | 409 MODEL_LEASED → :class:`LeaseHeldElsewhere`; 409 MODEL_NOT_RESIDENT → :class:`ModelNotResident` |
| ``POST /api/models/{id}/lease/renew``   | 200, the lease                | 404 LEASE_NOT_FOUND, 409 LEASE_EXPIRED → :class:`LeaseLost` |
| ``DELETE /api/models/{id}/lease``       | 200, the ended lease          | 404, 409 → :class:`LeaseLost`              |
| ``POST /v1/batches/{id}/lease``         | 200, ``lease_mode: caller``   | 404 lease_not_found, 409 batch_state_conflict → :class:`BatchReattachRefused` |

A route that does not exist (a pre-Feature-29 miLLM answers FastAPI's bare ``{"detail": ...}``
404) is :class:`LeaseUnsupported`: the run proceeds unpinned (P-13). The lease ID travels only in
the ``X-miLLM-Lease`` header, never a path or a log line (miLLM FR-29.1.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .endpoint_caller import CallResponse, EndpointCaller
from .endpoint_errors import LeaseLost, ModelNotResident, ProtocolUnsupported


class LeaseUnsupported(Exception):
    """The server serves no lease routes (P-13: run unpinned)."""


class LeaseHeldElsewhere(Exception):
    def __init__(self, holder: str | None, expires_at: str | None) -> None:
        super().__init__(f"the model is leased by {holder or 'another holder'} until {expires_at}")
        self.holder = holder
        self.expires_at = expires_at


class BatchReattachRefused(Exception):
    def __init__(self, batch_id: str, status: int, code: str | None) -> None:
        super().__init__(f"miLLM refused to re-attach batch {batch_id}: {status} {code or ''}")
        self.batch_id = batch_id
        self.status = status
        self.code = code


@dataclass(frozen=True)
class LeaseGrant:
    lease_id: str = field(repr=False)
    model_id: int = 0
    model_name: str = ""
    expires_at: datetime | None = None
    ttl_seconds: int = 0


def _error(response: CallResponse) -> tuple[str | None, str, dict[str, Any]]:
    body = response.body if isinstance(response.body, dict) else {}
    err = body.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        details = err.get("details")
        return (
            str(code).upper() if code else None,
            str(err.get("message") or ""),
            details if isinstance(details, dict) else {},
        )
    return None, str(body.get("detail") or ""), {}


def _data(response: CallResponse) -> dict[str, Any]:
    body = response.body if isinstance(response.body, dict) else {}
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProtocolUnsupported("a lease route answered without data")
    return data


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def acquire(
    caller: EndpointCaller, model_id: int, *, holder: str, reason: str, ttl_seconds: int
) -> LeaseGrant:
    response = caller.raw(
        "POST",
        f"/api/models/{model_id}/lease",
        body={"holder": holder, "reason": reason[:512], "ttl_seconds": ttl_seconds},
    )
    if response.status in (200, 201):
        data = _data(response)
        lease_id = data.get("lease_id")
        if not isinstance(lease_id, str) or not lease_id:
            raise ProtocolUnsupported("the lease grant carries no lease_id")
        return LeaseGrant(
            lease_id,
            int(data.get("model_id") or model_id),
            str(data.get("model_name") or ""),
            _when(data.get("expires_at")),
            int(data.get("ttl_seconds") or ttl_seconds),
        )
    code, message, details = _error(response)
    if response.status == 404 and code is None:
        raise LeaseUnsupported(message or "no lease route")
    if response.status == 409 and code == "MODEL_LEASED":
        raise LeaseHeldElsewhere(details.get("holder"), details.get("expires_at"))
    if response.status == 409 and code == "MODEL_NOT_RESIDENT":
        raise ModelNotResident(message, None, None)
    raise ProtocolUnsupported(f"lease acquire answered {response.status} {code or ''}: {message}")


def renew(
    caller: EndpointCaller, model_id: int, lease_id: str, *, ttl_seconds: int
) -> datetime | None:
    response = caller.raw(
        "POST",
        f"/api/models/{model_id}/lease/renew",
        body={"ttl_seconds": ttl_seconds},
        lease_id=lease_id,
    )
    if response.status == 200:
        return _when(_data(response).get("expires_at"))
    code, message, _ = _error(response)
    if response.status in (404, 409):
        raise LeaseLost(message or "the lease is gone", (code or "lease_gone").lower())
    raise ProtocolUnsupported(f"lease renew answered {response.status} {code or ''}: {message}")


def release(caller: EndpointCaller, model_id: int, lease_id: str) -> None:
    response = caller.raw("DELETE", f"/api/models/{model_id}/lease", lease_id=lease_id)
    if response.status == 200:
        return
    code, message, _ = _error(response)
    if response.status in (404, 409):
        raise LeaseLost(message or "the lease had already ended", (code or "lease_gone").lower())
    raise ProtocolUnsupported(f"lease release answered {response.status} {code or ''}: {message}")


def reattach_batch(caller: EndpointCaller, batch_id: str, lease_id: str) -> dict[str, Any]:
    """``POST /v1/batches/{id}/lease`` with the new lease (miLLM FR-26.7.7)."""
    response = caller.raw("POST", f"/v1/batches/{batch_id}/lease", openai=True, lease_id=lease_id)
    if response.status == 200 and isinstance(response.body, dict):
        return response.body
    body = response.body if isinstance(response.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    raise BatchReattachRefused(batch_id, response.status, err.get("code") if err else None)
