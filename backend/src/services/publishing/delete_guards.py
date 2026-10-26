"""Feature 008's answers to 002's version delete (002 FR-002.37; ``REFERENCE_CHECKERS``).

A version delete is a tombstone: the row, its manifest and every 008 record that references it
stay. What must refuse it:

- ``dw_publishes``: a VERIFIED publish (the Hub holds this version; deleting it here would leave
  the published record describing a version the app says is gone), or a publish still running;
- ``dw_publish_builds`` and ``dw_exports``: a build or export still writing from its files;
- ``dw_publish_check_runs``: a check run still queued against it.

Finished builds, exports and check runs do not refuse: they are evidence, kept with the tombstone.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.publish import (
    ACTIVE_PUBLISH_STATUSES,
    BuildStatus,
    CheckRunStatus,
    Export,
    ExportStatus,
    Publish,
    PublishBuild,
    PublishCheckRun,
    PublishStatus,
)
from ..version_delete_service import Reference, ReferenceChecker, register_reference_checker


async def _publishes(db: AsyncSession, version_id: str) -> Reference | None:
    row = (
        await db.execute(
            select(Publish.id, Publish.repo_id, Publish.status).where(
                Publish.version_id == version_id,
                Publish.status.in_((PublishStatus.PUBLISHED, *ACTIVE_PUBLISH_STATUSES)),
            )
        )
    ).first()
    if row is None:
        return None
    published = row.status == PublishStatus.PUBLISHED
    return Reference(
        "version_published" if published else "version_in_use",
        (
            f"Version {version_id} is published to {row.repo_id} (publish {row.id}); the Hub copy "
            "is the record of what was shipped, so the version is kept."
            if published
            else f"Publish {row.id} to {row.repo_id} is still running; wait for it to finish."
        ),
        {"publish_id": row.id, "repo_id": row.repo_id},
    )


async def _builds(db: AsyncSession, version_id: str) -> Reference | None:
    found = (
        await db.execute(
            select(PublishBuild.id).where(
                PublishBuild.version_id == version_id,
                PublishBuild.status.in_((BuildStatus.QUEUED, BuildStatus.BUILDING)),
            )
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    return Reference(
        "version_in_use", f"Publish build {found} is reading this version.", {"build_id": found}
    )


async def _exports(db: AsyncSession, version_id: str) -> Reference | None:
    found = (
        await db.execute(
            select(Export.id).where(
                Export.version_id == version_id,
                Export.status.in_((ExportStatus.QUEUED, ExportStatus.RUNNING)),
            )
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    return Reference(
        "version_in_use", f"Export {found} is reading this version.", {"export_id": found}
    )


async def _check_runs(db: AsyncSession, version_id: str) -> Reference | None:
    found = (
        await db.execute(
            select(PublishCheckRun.id).where(
                PublishCheckRun.version_id == version_id,
                PublishCheckRun.status == CheckRunStatus.QUEUED,
            )
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    return Reference(
        "version_in_use", f"Check run {found} is reading this version.", {"check_run_id": found}
    )


for _table, _check in (
    ("dw_publishes", _publishes),
    ("dw_publish_builds", _builds),
    ("dw_exports", _exports),
    ("dw_publish_check_runs", _check_runs),
):
    register_reference_checker(ReferenceChecker("008", _table, _check))
