"""Agent activity: write and summarise ``dw_agent_requests`` (010 FTDD sections 4.2, 5.6; T-51).

The write happens after the response is built and never fails the request: a lost activity row
costs one count on the Agent access card, a failed request costs the agent its action.

A *session* is one identity with no gap above 30 minutes between requests (T-51, FTID IQ8).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.database import async_session_factory
from ..models.agent_request import AgentRequest

logger = logging.getLogger(__name__)

SESSION_GAP = timedelta(minutes=30)
ACTIVITY_WINDOW = timedelta(hours=1)


async def record_agent_request(identity: str, method: str, route: str, status_code: int) -> None:
    """Insert one activity row in its own session. Logs a warning on failure; never raises."""
    try:
        async with async_session_factory()() as db:
            db.add(
                AgentRequest(
                    id=str(uuid.uuid4()),
                    identity=identity,
                    method=method[:8],
                    route=route,
                    status_code=status_code,
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001 - activity is best-effort by design (FTDD 4.2)
        logger.warning("Could not record agent request %s %s for %s", method, route, identity)


def count_sessions(rows: list[tuple[str, Any]]) -> int:
    """Sessions in ``(identity, created_at)`` rows: a gap above 30 minutes starts a new one."""
    last: dict[str, Any] = {}
    sessions = 0
    for identity, created_at in sorted(rows, key=lambda r: (r[0], r[1])):
        previous = last.get(identity)
        if previous is None or created_at - previous > SESSION_GAP:
            sessions += 1
        last[identity] = created_at
    return sessions


async def activity_summary(db: AsyncSession) -> dict[str, Any]:
    """Identities, sessions and requests over the last hour, for the card."""
    since = utc_now() - ACTIVITY_WINDOW
    rows = [
        (identity, created_at)
        for identity, created_at in (
            await db.execute(
                select(AgentRequest.identity, AgentRequest.created_at).where(
                    AgentRequest.created_at > since
                )
            )
        ).all()
    ]
    return {
        "window": "last hour",
        "identities": sorted({identity for identity, _ in rows}),
        "sessions": count_sessions(rows),
        "requests": len(rows),
    }


def prune_agent_requests(db: Session) -> int:
    """Delete activity rows older than the retention (30 days by default)."""
    cutoff = utc_now() - timedelta(days=get_settings().agent_request_retention_days)
    result = db.execute(
        delete(AgentRequest)
        .where(AgentRequest.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
