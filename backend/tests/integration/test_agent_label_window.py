"""The P-07 agent labelling window on real PostgreSQL (010 FTASKS 5.5, build-side half).

Rules (010 FTDD section 5.4): strict ``>`` against the threshold; the window bounds the sum; the
scope (version or source), never the identity, keys it; the check and the admit hold one advisory
lock, so two concurrent runs cannot both slip under the threshold. Label-run cases (resume, cached
reuse) wait on feature 005.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from src.core.database import async_session_factory, get_sync_engine
from src.services import agent_label_ledger as ledger


async def _admit(scope: str, identity: str, rows: int) -> None:
    async with async_session_factory()() as db:
        await ledger.admit(
            db, scope, identity, "probe_verdict", f"run_{uuid.uuid4().hex[:8]}", rows
        )
        await db.commit()


async def _needs(scope: str, rows: int) -> bool:
    async with async_session_factory()() as db:
        result = await ledger.needs_approval(db, scope, rows)
        await db.rollback()
        return result


async def test_the_threshold_is_strict(clean_db: None) -> None:
    scope = str(uuid.uuid4())
    await _admit(scope, "agent:a", 4000)
    assert await _needs(scope, 1000) is False, "exactly 5,000 rows does not need approval"
    assert await _needs(scope, 1001) is True, "5,001 rows does"


async def test_two_scopes_do_not_share_and_two_identities_do(clean_db: None) -> None:
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    await _admit(first, "agent:a", 3000)
    await _admit(first, "agent:b", 2000)
    assert await _needs(first, 1) is True, "two identities share one version's budget"
    assert await _needs(second, 5000) is False, "another version's total is separate"


async def test_a_run_outside_the_window_does_not_count(clean_db: None) -> None:
    scope = str(uuid.uuid4())
    await _admit(scope, "agent:a", 5000)
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_agent_label_rows SET admitted_at = now() - interval '25 hours'")
        )
    assert await _needs(scope, 5000) is False


async def test_two_concurrent_runs_cannot_both_slip_under(clean_db: None) -> None:
    """Each run checks then admits inside one transaction holding the scope's advisory lock."""
    scope = str(uuid.uuid4())
    outcomes: list[bool] = []

    async def run() -> None:
        async with async_session_factory()() as db:
            gated = await ledger.needs_approval(db, scope, 3000)
            outcomes.append(gated)
            await asyncio.sleep(0.2)  # hold the lock while the other run waits
            if not gated:
                await ledger.admit(db, scope, "agent:a", "probe_verdict", "r", 3000)
            await db.commit()

    await asyncio.gather(run(), run())
    assert sorted(outcomes) == [False, True], outcomes
