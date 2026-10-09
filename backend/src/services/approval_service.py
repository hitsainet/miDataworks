"""The approval queue (ADR-013; Foundation tasks 9.1, 9.3).

Lifecycle: ``pending`` → ``executing`` → ``executed`` | ``failed``; or ``pending`` → ``rejected``
| ``expired``. Approving runs the stored request EXACTLY ONCE: the row is locked
(``SELECT … FOR UPDATE``), moved to ``executing`` and committed before the executor runs, so a
second approve — concurrent or later — finds a non-pending row and is refused with ``409``.

The executor is an in-process call of the original route function, never an HTTP self-call: a
self-call would have to omit or forge the agent header and would re-enter the gate (miStudio's
approve route self-called its steering endpoint).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..core.agent_origin import EXECUTORS, Actor, pop_secret, put_value
from ..core.canonical_json import canonical_json, canonical_sha256
from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.encryption import decrypt_value, derive_subkey, encrypt_value
from ..core.errors import AppError, ConflictError, NotFoundError
from ..core.ids import new_id
from ..models.approval import Approval

logger = logging.getLogger(__name__)

_DIGEST_KEY_INFO = b"approval-digest-hmac"


def _hmac(value: str) -> str:
    return (
        "hmac-sha256:"
        + hmac.new(
            derive_subkey(_DIGEST_KEY_INFO), value.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    )


class ApprovalService:
    @staticmethod
    async def create_pending(
        db: AsyncSession,
        *,
        action: str,
        executor_key: str,
        payload: dict[str, Any],
        secret_fields: tuple[str, ...],
        requested_by: str,
        summary: str,
    ) -> Approval:
        stored = json.loads(canonical_json(payload))
        secrets: dict[str, str] = {}
        for path in secret_fields:
            value = pop_secret(stored, path)
            if value is not None:
                secrets[path] = str(value)
                # A plain sha256 of an API key would be a brute-force target; an HMAC is not.
                put_value(stored, path, _hmac(str(value)))
        approval = Approval(
            id=new_id("apr"),
            action=action,
            target=executor_key,
            summary=summary,
            payload=stored,
            request_digest=canonical_sha256(stored),
            secret_payload=encrypt_value(json.dumps(secrets)) if secrets else None,
            requested_by=requested_by,
            status="pending",
            expires_at=utc_now() + timedelta(hours=get_settings().approval_ttl_hours),
        )
        db.add(approval)
        await db.commit()
        logger.info("Approval %s created for %s by %s", approval.id, action, requested_by)
        return approval

    @staticmethod
    async def get(db: AsyncSession, approval_id: str) -> Approval:
        approval = await db.get(Approval, approval_id)
        if approval is None:
            raise NotFoundError(f"No approval {approval_id}.")
        return approval

    @staticmethod
    async def list(db: AsyncSession, status: str | None = None) -> list[Approval]:
        query = select(Approval).order_by(Approval.created_at.desc())
        if status:
            query = query.where(Approval.status == status)
        return list((await db.execute(query)).scalars())

    @staticmethod
    async def approve(db: AsyncSession, approval_id: str, decided_by: str) -> Approval:
        row = (
            await db.execute(select(Approval).where(Approval.id == approval_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"No approval {approval_id}.")
        if row.status == "pending" and row.expires_at <= utc_now():
            row.status = "expired"
            row.secret_payload = None
            await db.commit()
        if row.status != "pending":
            raise ConflictError(
                f"Approval {approval_id} is {row.status}; only a pending approval can be approved.",
                code="APPROVAL_NOT_PENDING",
                details={"status": row.status},
            )
        executor = EXECUTORS.get(row.target)
        if executor is None:
            raise ConflictError(
                f"Approval {approval_id} names an action this server no longer serves.",
                code="APPROVAL_EXECUTOR_MISSING",
            )
        if canonical_sha256(row.payload) != row.request_digest:
            row.status = "failed"
            row.error = {"code": "APPROVAL_TAMPERED", "message": "The stored request changed."}
            row.secret_payload = None
            await db.commit()
            raise ConflictError(
                "The stored request no longer matches its digest.", code="APPROVAL_TAMPERED"
            )

        row.status = "executing"
        row.decided_by = decided_by
        row.decided_at = utc_now()
        secret_payload = row.secret_payload
        row.secret_payload = None
        payload = json.loads(json.dumps(row.payload))
        requested_by = row.requested_by
        await db.commit()  # the lock is released with status already "executing"

        try:
            if secret_payload:
                for path, value in json.loads(decrypt_value(secret_payload)).items():
                    put_value(payload, path, value)
            kwargs: dict[str, Any] = {
                name: TypeAdapter(annotation).validate_python(payload.get(name))
                for name, annotation in executor.value_params.items()
            }
            kwargs[executor.db_param] = db
            kwargs[executor.actor_param] = Actor(
                origin="agent", agent=requested_by, approval_id=approval_id
            )
            result = await executor.fn(**kwargs)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            error = (
                {"code": exc.code, "message": exc.message}
                if isinstance(exc, AppError)
                else {"code": "EXECUTION_FAILED", "message": "The approved action failed."}
            )
            if not isinstance(exc, AppError):
                logger.exception("Approved action %s failed", approval_id)
            await db.execute(
                update(Approval)
                .where(Approval.id == approval_id)
                .values(status="failed", error=error)
            )
            await db.commit()
            return await ApprovalService.get(db, approval_id)

        result_id, result_kind = _result_reference(result)
        await db.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(status="executed", result_id=result_id, result_kind=result_kind)
        )
        await db.commit()
        refreshed = await ApprovalService.get(db, approval_id)
        await db.refresh(refreshed)
        return refreshed

    @staticmethod
    async def reject(db: AsyncSession, approval_id: str, decided_by: str, reason: str) -> Approval:
        row = (
            await db.execute(select(Approval).where(Approval.id == approval_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(f"No approval {approval_id}.")
        if row.status != "pending":
            raise ConflictError(
                f"Approval {approval_id} is {row.status}; only a pending approval can be rejected.",
                code="APPROVAL_NOT_PENDING",
                details={"status": row.status},
            )
        row.status = "rejected"
        row.reason = reason
        row.decided_by = decided_by
        row.decided_at = utc_now()
        row.secret_payload = None
        await db.commit()
        return row


def _result_reference(result: Any) -> tuple[str | None, str | None]:
    if isinstance(result, BaseModel):
        result = result.model_dump()
    if isinstance(result, dict):
        rid = result.get("id") or result.get("key") or result.get("role")
        if isinstance(rid, str):
            kind = rid.split("_", 1)[0] if "_" in rid else "setting"
            return rid[:64], kind[:32]
    return None, None


def expire_due(db: Session) -> int:
    """Mark pending approvals past ``expires_at`` as expired and clear their secrets (P-08)."""
    result = db.execute(
        update(Approval)
        .where(Approval.status == "pending", Approval.expires_at <= utc_now())
        .values(status="expired", secret_payload=None)
    )
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
