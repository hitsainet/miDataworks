"""Feature 007 installs itself into the registries other features declared (FTID 007 section 1).

- 002's binding resolvers: ``generation_run`` bindings resolve to completed generation runs
  (``bindings.OWNERS`` already names feature 007);
- 002's delete guard: a version a queued or running generation run reads cannot be deleted;
- 006's metric registry: the four gate-3 diversity figures and their two controls.

005's label-run preflight is registered by import (``label_run_preflight_registrations``), and
the operators by ``operators/native/registrations.py``. :func:`install` runs at API start (the
lifespan), at worker start (``worker_process_init``) and when the generation routes are imported
(an ASGI test client runs no lifespan). A reachability test removes each call and requires a red.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.generation import GenerationRun
from ..bindings import register_binding_resolver
from ..version_delete_service import Reference, ReferenceChecker, register_reference_checker
from . import metric_registration
from .run_service import binding_record

logger = logging.getLogger(__name__)


async def _live_generation_runs(db: AsyncSession, version_id: str) -> Reference | None:
    found = (
        await db.execute(
            select(GenerationRun.id).where(
                GenerationRun.input_version_id == version_id,
                GenerationRun.state.in_(("queued", "running")),
            )
        )
    ).first()
    if found is None:
        return None
    return Reference(
        "version_in_use",
        f"Generation run {found[0]} is generating from this version. Wait for it to finish or "
        "cancel it, then delete.",
        {"generation_run_id": found[0]},
    )


async def _diversity_reports(db: AsyncSession, version_id: str) -> Reference | None:
    """A diversity report never blocks a delete: it is a fact about an immutable version, and the
    tombstone keeps the row it points at (FR-002.37)."""
    del db, version_id
    return None


def install() -> None:
    register_binding_resolver("generation_run", binding_record)
    register_reference_checker(ReferenceChecker("007", "dw_generation_runs", _live_generation_runs))
    register_reference_checker(ReferenceChecker("007", "dw_diversity_reports", _diversity_reports))
    metric_registration.register()
    logger.debug("feature 007 installed: generation_run bindings, delete guard, gate-3 metrics")
