"""Delete as tombstone, and verify-rebuild requests (FR-002.6, FR-002.37; FTID 002 section 3.9).

Delete is refused, in this order, by the first rule that applies:
1. a running job reads the version (each job kind that reads versions registers a reader);
2. a reference checker reports a use — feature 008's verified publish, feature 009's detector-set
   role — through :data:`REFERENCE_CHECKERS`, which each owning feature populates at import.

Then one transaction marks the tombstone (the immutability trigger permits exactly that), and only
after it commits are ``versions/<id>/`` files removed. A failed removal leaves a tombstone with files,
which the orphan sweeper's second rule removes. The manifest, counts and events stay, so lineage
questions still resolve, and a version number is never reused (P-15).

``tests/integration/test_version_delete.py`` fails if a table outside feature 002 references
``dw_versions`` without a registered checker: no hand-kept list of referencing tables.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.clock import utc_now
from ..core.errors import ConflictError
from ..core.ids import new_id
from ..core.storage import version_dir
from ..models.enums import VersionState
from ..models.job import Job
from ..models.version import Version, VersionBuild
from .version_read_service import get_version_row

logger = logging.getLogger(__name__)

LIVE = ("queued", "running", "cancelling")


@dataclass(frozen=True)
class Reference:
    code: str
    message: str
    details: dict[str, Any]


Checker = Callable[[AsyncSession, str], Awaitable[Reference | None]]


@dataclass(frozen=True)
class ReferenceChecker:
    """``table`` is the referencing table the checker reads (the registry is keyed on it)."""

    owner: str
    table: str
    check: Checker


#: table -> checker. Features 008 and 009 register theirs when they add their tables.
REFERENCE_CHECKERS: dict[str, ReferenceChecker] = {}

#: The tables feature 002 itself owns that reference dw_versions; they need no checker.
OWN_TABLES = frozenset(
    {
        "dw_versions",
        "dw_version_inputs",
        "dw_version_steps",
        "dw_version_builds",
        "dw_version_verifications",
        "dw_version_comparisons",
    }
)


def register_reference_checker(checker: ReferenceChecker) -> None:
    REFERENCE_CHECKERS[checker.table] = checker


async def _jobs_reading(db: AsyncSession, version_id: str) -> list[str]:
    """Live build and verify jobs whose request reads this version (the 002 job kinds' reader)."""
    rows = await db.execute(
        select(Job.id)
        .join(VersionBuild, VersionBuild.job_id == Job.id)
        .where(
            Job.status.in_(LIVE),
            (VersionBuild.verify_version_id == version_id)
            | VersionBuild.request["inputs"].contains(
                cast([{"kind": "version", "version_id": version_id}], JSONB)
            ),
        )
    )
    return [str(r) for r in rows.scalars()]


async def delete(db: AsyncSession, who: Who, version_id: str, reason: str) -> Version:
    version = await get_version_row(db, version_id)
    if version.state == VersionState.DELETED:
        return version
    readers = await _jobs_reading(db, version.id)
    if readers:
        raise ConflictError(
            f"Version {version.number} is being read by job {readers[0]}. Wait for it to finish or "
            "cancel it, then delete.",
            code="version_in_use",
            details={"jobs": readers},
        )
    for checker in REFERENCE_CHECKERS.values():
        found = await checker.check(db, version.id)
        if found is not None:
            raise ConflictError(found.message, code=found.code, details=found.details)
    version.state = VersionState.DELETED
    version.deleted_by = who.who
    version.deleted_by_origin = who.origin
    version.deleted_at = utc_now()
    version.delete_reason = reason
    await db.commit()
    shutil.rmtree(version_dir(version.id), ignore_errors=True)  # after the commit; sweeper backstop
    logger.info("version %s tombstoned by %s (%s)", version.id, who.who, who.origin)
    return version


async def request_verify(db: AsyncSession, who: Who, version_id: str) -> str:
    """Create a ``version_verify`` job: the version's own request, rebuilt with reuse off."""
    version = await get_version_row(db, version_id)
    if version.state == VersionState.DELETED:
        raise ConflictError(
            f"Version {version.number} was deleted; there is nothing to verify against.",
            code="version_deleted",
            details={"version_id": version.id},
        )
    original = await db.get(VersionBuild, version.build_job_id)
    assert original is not None, "every version has its build record"
    job = Job(
        id=new_id("job"),
        kind="version_verify",
        status="queued",
        progress=0.0,
        params={"version_id": version.id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    db.add(job)
    await db.flush()
    db.add(
        VersionBuild(
            job_id=job.id,
            dataset_id=version.dataset_id,
            request_digest=version.request_digest,
            request=dict(original.request),
            reuse_enabled=False,
            verify_version_id=version.id,
            sources_verified=False,
            steps=[],
            current_step_index=0,
        )
    )
    await db.commit()
    return job.id
