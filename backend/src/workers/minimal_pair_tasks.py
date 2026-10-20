"""Beat advances every running minimal-pair chain (009; operator decision 2026-10-07).

``midataworks.detector_sets.advance_minimal_pair_chains`` (``default`` queue: it starts work, it
does none) looks at each running chain once: a stage that completed starts the next, a stage that
failed stops the chain naming it. A chain is a row, not a job, so it has no heartbeat to miss while
a long judge run works: each stage is a job of its own, with its own janitor.

The services the chain calls are async (they are the API's own); a Beat tick runs them on a
dedicated engine with no pool, so no connection outlives the tick's event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ..core.celery_app import celery_app

logger = logging.getLogger(__name__)

ADVANCE_ALL_TASK = "midataworks.detector_sets.advance_minimal_pair_chains"

T = TypeVar("T")


def run_async(work: Callable[[Any], Awaitable[T]]) -> T:
    """Run ``work(session)`` on a fresh async engine inside its own event loop."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from ..core.config import get_settings
    from ..core.database import SESSION_AUTOFLUSH

    async def main() -> T:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=SESSION_AUTOFLUSH)
        try:
            async with factory() as session:
                return await work(session)
        finally:
            await engine.dispose()

    return asyncio.run(main())


async def advance_running(session: Any) -> dict[str, str]:
    """Advance every running chain; one chain's error never stops the others."""
    from ..services.detector_sets import minimal_pair_chain

    out: dict[str, str] = {}
    for chain_id in await minimal_pair_chain.running_ids(session):
        try:
            chain = await minimal_pair_chain.advance(session, chain_id)
            out[chain_id] = f"{chain.state}:{chain.stage}"
        except Exception:  # noqa: BLE001 - logged; the next tick tries again
            logger.exception("minimal_pair_chain.advance_failed chain=%s", chain_id)
            await session.rollback()
            out[chain_id] = "error"
    return out


@celery_app.task(name=ADVANCE_ALL_TASK, acks_late=True)
def advance_minimal_pair_chains() -> dict[str, str]:
    return run_async(advance_running)
