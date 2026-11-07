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

import builtins
import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func, select, update
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
    async def list(
        db: AsyncSession,
        status: str | None = None,
        include_archived: bool = False,
        *,
        decided: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Approval]:
        """Approvals, newest first. ``decided`` lists the History (ENH-008 D6): every request that
        is no longer pending, the most recent decision first (an expiry counts as decided at its
        ``expires_at``). ``limit``/``offset`` page it; no limit lists everything."""
        query = select(Approval)
        if decided:
            decided_when = func.coalesce(Approval.decided_at, Approval.expires_at)
            query = query.where(Approval.status != "pending").order_by(
                decided_when.desc(), Approval.id.desc()
            )
        else:
            query = query.order_by(Approval.created_at.desc(), Approval.id.desc())
        if not include_archived:
            query = query.where(Approval.archived_at.is_(None))
        if status:
            query = query.where(Approval.status == status)
        if offset:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return list((await db.execute(query)).scalars())

    @staticmethod
    async def approve(
        db: AsyncSession, approval_id: str, decided_by: str, *, batch_id: str | None = None
    ) -> Approval:
        row = (
            await db.execute(
                select(Approval)
                .where(Approval.id == approval_id)
                .with_for_update()
                # The session keeps objects across commits (expire_on_commit=False): a row read
                # earlier in this session (the route's action check, a batch's pre-check) would
                # otherwise keep its stale "pending" and the request could run twice.
                .execution_options(populate_existing=True)
            )
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
        row.batch_id = batch_id
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
    async def approve_batch(
        db: AsyncSession,
        approval_ids: builtins.list[str],
        decided_by: str,
        *,
        eligible: Callable[[Approval], bool],
        kind_label: str = "settings change",
    ) -> tuple[str, builtins.list[dict[str, Any]]]:
        """Approve several waiting requests of ONE kind in ONE operator decision (ENH-006 D79, D81).

        Every id is checked first: an unknown id, a request that is not pending (or has expired),
        or one ``eligible`` refuses makes the whole call ``409 APPROVAL_BATCH_REFUSED``, naming
        each, and NOTHING is approved. Then each request goes through :meth:`approve`, the single
        approve path: it runs exactly once, is recorded on its own row as decided by the operator,
        and carries the shared ``batch_id``. A request that fails while it runs (or was decided by
        someone else in between) is reported in its result and does not stop the others."""
        ids: builtins.list[str] = builtins.list(dict.fromkeys(approval_ids))
        rows = {
            a.id: a
            for a in (await db.execute(select(Approval).where(Approval.id.in_(ids)))).scalars()
        }
        now = utc_now()
        refused: builtins.list[dict[str, str]] = []
        for approval_id in ids:
            row = rows.get(approval_id)
            if row is None:
                refused.append({"approval_id": approval_id, "reason": "not found"})
            elif row.status != "pending" or row.expires_at <= now:
                status = row.status if row.status != "pending" else "expired"
                refused.append({"approval_id": approval_id, "reason": f"not pending ({status})"})
            elif not eligible(row):
                refused.append(
                    {"approval_id": approval_id, "reason": f"not a {kind_label} ({row.action})"}
                )
        if refused:
            raise ConflictError(
                f"Only waiting requests of one kind ({kind_label}) can be approved together; "
                "nothing was approved. "
                "Refused: " + ", ".join(f"{r['approval_id']} ({r['reason']})" for r in refused),
                code="APPROVAL_BATCH_REFUSED",
                details={"refused": refused},
            )

        batch_id = new_id("apb")
        results: builtins.list[dict[str, Any]] = []
        for approval_id in ids:
            try:
                row = await ApprovalService.approve(db, approval_id, decided_by, batch_id=batch_id)
            except AppError as exc:
                await db.rollback()
                current = await db.get(Approval, approval_id)
                results.append(
                    {
                        "approval_id": approval_id,
                        "status": current.status if current is not None else "unknown",
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )
                continue
            results.append(
                {
                    "approval_id": approval_id,
                    "status": row.status,
                    "result_kind": row.result_kind,
                    "result_id": row.result_id,
                    "error": row.error,
                }
            )
        logger.info("Approval batch %s: %d requests by %s", batch_id, len(ids), decided_by)
        return batch_id, results

    @staticmethod
    async def reject(db: AsyncSession, approval_id: str, decided_by: str, reason: str) -> Approval:
        row = (
            await db.execute(
                select(Approval)
                .where(Approval.id == approval_id)
                .with_for_update()
                # The session keeps objects across commits (expire_on_commit=False): a row read
                # earlier in this session (the route's action check, a batch's pre-check) would
                # otherwise keep its stale "pending" and the request could run twice.
                .execution_options(populate_existing=True)
            )
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
    """The created resource's id and kind, so the approval names what it made.

    017 journey finding (2026-10-28): a route that answers a ``JSONResponse`` (an approved label
    run start answers 201 with the run) recorded nothing, so an agent polling the approval could
    not learn which run it started; the response's JSON body is read like a returned model."""
    if isinstance(result, JSONResponse):
        try:
            result = json.loads(bytes(result.body))
        except ValueError:
            return None, None
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
