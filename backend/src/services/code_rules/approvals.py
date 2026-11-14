"""The per-hash approval list (FR-018.29, FR-018.30; the ``operators/allowlist.py`` shape).

Current state of a hash = its LATEST row: ``approved`` or ``revoked``; ``unknown`` with no row.
Read with a FRESH query on every call (the build-time read joins in 018 slice 4): no process caches
it, so a revocation written between two build steps is honoured by the second (M15). Writes are
the operator's only - the database CHECK refuses any other origin, whatever a route forgets.
``pending`` is not a row here: it is an agent's save waiting in ``dw_approvals``.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...models.code_rule import CodeRuleApproval

State = Literal["approved", "revoked", "unknown"]

_LATEST = select(CodeRuleApproval.action).order_by(CodeRuleApproval.id.desc()).limit(1)
_PENDING = text(
    "SELECT id FROM dw_approvals WHERE action = 'code_rule_save' AND status = 'pending' "
    "AND payload -> 'facts' ->> 'code_sha256' = :sha ORDER BY created_at, id"
)


def _state(action: str | None) -> State:
    if action == "approve":
        return "approved"
    if action == "revoke":
        return "revoked"
    return "unknown"


async def state(db: AsyncSession, code_sha256: str) -> State:
    """The hash's current state, read now (never cached)."""
    action = (
        await db.execute(_LATEST.where(CodeRuleApproval.code_sha256 == code_sha256))
    ).scalar_one_or_none()
    return _state(action)


async def pending(db: AsyncSession, code_sha256: str) -> list[str]:
    """Agent saves of this hash waiting for the operator (``dw_approvals`` ids)."""
    return [str(r) for r in (await db.execute(_PENDING, {"sha": code_sha256})).scalars()]


async def history(db: AsyncSession, code_sha256: str) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(CodeRuleApproval)
            .where(CodeRuleApproval.code_sha256 == code_sha256)
            .order_by(CodeRuleApproval.id.desc())
        )
    ).scalars()
    return [
        {
            "id": r.id,
            "action": r.action,
            "reason": r.reason,
            "changed_by": r.changed_by,
            "approval_id": r.approval_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


async def write(
    db: AsyncSession,
    code_sha256: str,
    action: Literal["approve", "revoke"],
    reason: str,
    changed_by: str,
    approval_id: str | None = None,
) -> CodeRuleApproval:
    """Append one operator decision. The caller commits."""
    row = CodeRuleApproval(
        code_sha256=code_sha256,
        action=action,
        reason=reason.strip(),
        changed_by=changed_by,
        origin="operator",
        approval_id=approval_id,
    )
    db.add(row)
    await db.flush()
    return row


# --- the same reads on a sync session (rule-set save, build resolution; feature 018 slice 4) -----

_LATEST_ROW = (
    select(CodeRuleApproval.action, CodeRuleApproval.id)
    .order_by(CodeRuleApproval.id.desc())
    .limit(1)
)


def latest_sync(session: Session, code_sha256: str) -> tuple[State, int | None]:
    """``(state, the approve row's id when approved)``, read now (never cached, M15/M19)."""
    row = session.execute(_LATEST_ROW.where(CodeRuleApproval.code_sha256 == code_sha256)).first()
    if row is None:
        return "unknown", None
    state = _state(str(row[0]))
    return state, (int(row[1]) if state == "approved" else None)


def pending_sync(session: Session, code_sha256: str) -> list[str]:
    return [str(r) for r in session.execute(_PENDING, {"sha": code_sha256}).scalars()]
