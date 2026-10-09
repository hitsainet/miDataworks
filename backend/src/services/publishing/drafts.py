"""The card draft and the handoff manifest the API serves (FR-008.8, FR-008.37; FTASKS 7.6, 9.7).

- ``GET /versions/{id}/handoff-manifest`` serves the PUBLISHED manifest of the version's latest
  successful publish, byte for byte as canonical JSON of what the publish record stored; when
  there is none, the BUILT manifest (``publication: null``) of the version's latest completed
  build. With neither, it refuses: a manifest describes files, and there are none yet.
- ``GET /versions/{id}/card-draft`` returns the locked front matter and record section, and the
  default prose, for the Publish screen. C-2 (the token) runs only in the worker, so the draft's
  check list leaves it out rather than guessing it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import utc_now
from ...core.errors import ConflictError
from ...models.publish import BuildStatus, PublishBuild
from . import card as card_mod
from .build_service import completed_build, version_or_refuse
from .check_inputs import any_forbids, assemble, manifest_source
from .checks import CheckOutcome, evaluate_checks
from .manifest_builder import (
    build_document,
    dataset_target,
    manifest_bytes,
    rows_content,
    timestamp,
)
from .publish_service import latest_published


def latest_build(session: Session, version_id: str) -> PublishBuild:
    build = session.execute(
        select(PublishBuild)
        .where(PublishBuild.version_id == version_id, PublishBuild.status == BuildStatus.COMPLETED)
        .order_by(PublishBuild.completed_at.desc(), PublishBuild.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if build is None:
        raise ConflictError(
            f"Version {version_id} has no completed build yet. Preview its files first.",
            code="build_required",
        )
    return build


def built_document(
    session: Session, version_id: str, build: PublishBuild, publication: dict[str, Any] | None
) -> tuple[dict[str, Any], list[CheckOutcome]]:
    from .publish_job import version_identity

    version = version_or_refuse(session, version_id)
    assert build.files is not None
    assembled = assemble(
        session,
        version,
        label_column=build.projection["label_column"],
        visibility="private",
        token_scope="write",  # noqa: S106 - C-2 is dropped below; it runs only in the worker
    )
    outcomes = [o for o in evaluate_checks(assembled.inputs) if o.check != "C-2"]
    caveats = card_mod.caveats_from_outcomes(outcomes)
    if any_forbids(assembled.sources):
        caveats.append(
            {
                "code": "evaluation_only",
                "severity": "amber",
                "message": card_mod.EVALUATION_ONLY,
                "detail": {},
            }
        )
    document = build_document(
        version=version_identity(version, assembled.dataset.name),
        target=dataset_target(assembled.dataset.target_type),
        sources=[manifest_source(f) for f in assembled.sources],
        content=rows_content(
            build_files=build.files,
            columns=build.columns or [],
            label_column=build.projection["label_column"],
            omitted=build.omitted
            or {"excluded": 0, "flagged_unresolved": 0, "overrides_applied": 0},
            labelers=assembled.labelers,
            calibration=assembled.calibration,
        ),
        caveats=caveats,
        publication=publication,
        generated_at=build.completed_at,
        extensions={"lineage": assembled.lineage},
    )
    return document, outcomes


def handoff_manifest_bytes(session: Session, version_id: str) -> bytes:
    version = version_or_refuse(session, version_id)
    published = latest_published(session, version.id)
    if published is not None and published.published_manifest is not None:
        return manifest_bytes(published.published_manifest)
    document, _ = built_document(session, version.id, latest_build(session, version.id), None)
    return manifest_bytes(document)


def card_draft(
    session: Session, version_id: str, repo_id: str | None, build_id: str | None
) -> dict[str, Any]:
    version = version_or_refuse(session, version_id)
    build = (
        completed_build(session, build_id, version.id)
        if build_id
        else latest_build(session, version.id)
    )
    publication = (
        {"state": "in_repository", "repo_id": repo_id, "repo_type": "dataset"} if repo_id else None
    )
    document, outcomes = built_document(session, version.id, build, publication)
    assert build.files is not None
    record = card_mod.record_section(
        document,
        outcomes,
        drop_summary=version.drop_summary,
        digests=[
            {"path": f["path"], "bytes": f["bytes"], "sha256": f["sha256"]} for f in build.files
        ],
        history=[
            {
                "version": version.id,
                "commit": card_mod.THIS_COMMIT,
                "date": timestamp(utc_now())[:10],
            }
        ],
    )
    return {
        "front_matter": card_mod.front_matter(document),
        "record_markdown": record,
        "prose": card_mod.default_prose(document),
        "build_id": build.id,
    }
