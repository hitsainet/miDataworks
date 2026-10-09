"""Feature 008's tables (FTDD 008 section 4.1; FTASKS 4.1).

Facts recorded about a version after it exists live beside ``dw_versions``, never on it
(FR-002.3). A version's publication state is a query over ``dw_publishes``.

Database guards (migration ``0011``):
- ``dw_publishes``: a row that reached a terminal status never changes again, and no row is ever
  deleted; a partial unique index allows one non-terminal publish per repository (EC-7);
- ``dw_publish_files``: written once, never updated or deleted (the per-file evidence);
- ``dw_publish_check_runs``: a completed snapshot never changes (FR-008.15);
- ``dw_config_versions`` and ``dw_model_terms_notes``: insert-only.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import ActorOrigin, check_in


class BuildStatus(StrEnum):
    QUEUED = "queued"
    BUILDING = "building"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CheckRunStatus(StrEnum):
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishStatus(StrEnum):
    QUEUED = "queued"
    CHECKING = "checking"
    UPLOADING = "uploading"
    VERIFYING = "verifying"
    PUBLISHED = "published"
    NO_CHANGE = "no_change"
    REFUSED = "refused"
    VERIFICATION_FAILED = "verification_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_PUBLISH_STATUSES: tuple[str, ...] = ("queued", "checking", "uploading", "verifying")
TERMINAL_PUBLISH_STATUSES: frozenset[str] = frozenset(
    s.value for s in PublishStatus if s.value not in ACTIVE_PUBLISH_STATUSES
)


class PublishKind(StrEnum):
    PUBLISH = "publish"
    CARD_ONLY = "card_only"


class Visibility(StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class FileRole(StrEnum):
    SPLIT = "split"
    CARD = "card"
    MANIFEST = "manifest"


class ExportTarget(StrEnum):
    TRL = "trl"
    MIFORGE_SET = "miforge_set"
    REWARD_BUNDLE = "reward_bundle"


class ExportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConfigKind(StrEnum):
    SELECTOR = "selector"
    GRADER = "grader"


class TrainingOnOutputs(StrEnum):
    PERMITS = "permits"
    FORBIDS = "forbids"


_SHA = "~ '^[0-9a-f]{64}$'"


class PublishBuild(Base):
    __tablename__ = "dw_publish_builds"
    __table_args__ = (
        CheckConstraint(check_in("status", BuildStatus), name="status_valid"),
        CheckConstraint(f"projection_digest {_SHA}", name="projection_digest_pattern"),
        Index(
            "uq_dw_publish_builds_completed",
            "version_id",
            "projection_digest",
            unique=True,
            postgresql_where=text("status = 'completed'"),
        ),
        Index("ix_dw_publish_builds_version", "version_id", "projection_digest"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    projection_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The projection specification the digest describes (label column, columns, resolver).
    projection: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Per split: ``{name, path, rows, bytes, sha256, git_blob_sha1, logical_digest,
    #: label_counts, held_out, evaluation_only}``; null until the build completes.
    files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    columns: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    omitted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishCheckRun(Base):
    __tablename__ = "dw_publish_check_runs"
    __table_args__ = (
        CheckConstraint(check_in("status", CheckRunStatus), name="status_valid"),
        CheckConstraint(check_in("requested_visibility", Visibility), name="visibility_valid"),
        Index("ix_dw_publish_check_runs_version", "version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    build_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_publish_builds.id", ondelete="RESTRICT"), nullable=False
    )
    repo_id: Mapped[str] = mapped_column(Text, nullable=False)
    requested_visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    results: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    licence_table_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Publish(Base):
    """One publish attempt: the publication record (FR-008.23)."""

    __tablename__ = "dw_publishes"
    __table_args__ = (
        CheckConstraint(check_in("status", PublishStatus), name="status_valid"),
        CheckConstraint(check_in("kind", PublishKind), name="kind_valid"),
        CheckConstraint(check_in("requested_visibility", Visibility), name="visibility_valid"),
        CheckConstraint(
            "visibility_after IS NULL OR " + check_in("visibility_after", Visibility),
            name="visibility_after_valid",
        ),
        CheckConstraint(check_in("started_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        CheckConstraint(f"request_digest {_SHA}", name="request_digest_pattern"),
        CheckConstraint("commit IS NULL OR commit ~ '^[0-9a-f]{40}$'", name="commit_pattern"),
        CheckConstraint(
            "started_by_origin = 'operator' OR approval_id IS NOT NULL",
            name="agent_needs_approval",
        ),
        # A published record always carries its commit and its published manifest.
        CheckConstraint(
            "status <> 'published' OR (commit IS NOT NULL AND published_manifest IS NOT NULL "
            "AND visibility_after IS NOT NULL)",
            name="published_is_complete",
        ),
        Index(
            "uq_dw_publishes_active_repo",
            "repo_id",
            unique=True,
            postgresql_where=text(
                "status IN (" + ", ".join(f"'{s}'" for s in ACTIVE_PUBLISH_STATUSES) + ")"
            ),
        ),
        Index("ix_dw_publishes_version", "version_id"),
        Index("ix_dw_publishes_repo_created", "repo_id", text("created_at DESC")),
        Index("ix_dw_publishes_approval_digest", "approval_id", "request_digest"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    build_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_publish_builds.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The publish a card-only republish revises.
    parent_publish_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_publishes.id", ondelete="RESTRICT")
    )
    repo_id: Mapped[str] = mapped_column(Text, nullable=False)
    requested_visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    visibility_after: Mapped[str | None] = mapped_column(String(16))
    repo_existed: Mapped[bool | None] = mapped_column(Boolean)
    repo_was_private: Mapped[bool | None] = mapped_column(Boolean)
    head_before: Mapped[str | None] = mapped_column(String(40))
    commit: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    card_prose: Mapped[str] = mapped_column(Text, nullable=False)
    card_sha256: Mapped[str | None] = mapped_column(String(64))
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    published_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    check_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    licence_table_version: Mapped[int | None] = mapped_column(Integer)
    #: The approval digest of this request (FTDD 008 section 5.5).
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    #: 009's send (FK added by migration 0016, with 009's table).
    send_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("dw_detector_sends.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[str | None] = mapped_column(Text)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    cancel_too_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: ``{phase: seconds}`` measured by the worker (build, upload, verify).
    timings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishFile(Base):
    """One file of one publish, local and remote hashes side by side (FR-008.23)."""

    __tablename__ = "dw_publish_files"
    __table_args__ = (
        CheckConstraint(check_in("role", FileRole), name="role_valid"),
        CheckConstraint(f"sha256 {_SHA}", name="sha256_pattern"),
        CheckConstraint("git_blob_sha1 ~ '^[0-9a-f]{40}$'", name="git_blob_sha1_pattern"),
        CheckConstraint("bytes >= 0", name="bytes_nonnegative"),
    )

    publish_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_publishes.id", ondelete="RESTRICT"), primary_key=True
    )
    path_in_repo: Mapped[str] = mapped_column(Text, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    split: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    git_blob_sha1: Mapped[str] = mapped_column(String(40), nullable=False)
    remote_lfs_sha256: Mapped[str | None] = mapped_column(String(64))
    remote_blob_id: Mapped[str | None] = mapped_column(String(40))
    remote_size: Mapped[int | None] = mapped_column(BigInteger)
    match: Mapped[bool | None] = mapped_column(Boolean)


class Export(Base):
    __tablename__ = "dw_exports"
    __table_args__ = (
        CheckConstraint(check_in("target", ExportTarget), name="target_valid"),
        CheckConstraint(check_in("status", ExportStatus), name="status_valid"),
        CheckConstraint(check_in("started_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(started_by) > 0", name="started_by_present"),
        Index("ix_dw_exports_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT")
    )
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    config_version_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_config_versions.id", ondelete="RESTRICT")
    )
    rubric_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trl_version: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    files: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfigVersion(Base):
    """A selector or grader configuration version (FR-008.42). Insert-only."""

    __tablename__ = "dw_config_versions"
    __table_args__ = (
        CheckConstraint(check_in("kind", ConfigKind), name="kind_valid"),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint(f"body_sha256 {_SHA}", name="body_sha256_pattern"),
        CheckConstraint("number >= 1", name="number_positive"),
        UniqueConstraint("kind", "name", "number", name="uq_dw_config_versions_kind_name_number"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    plugin: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_config_versions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelTermsNote(Base):
    """An operator's note on whether a model's terms permit training on its outputs (FR-008.60).

    Operator-only (S3-08): the CHECK refuses an agent origin even if a route forgot to.
    """

    __tablename__ = "dw_model_terms_notes"
    __table_args__ = (
        CheckConstraint(check_in("training_on_outputs", TrainingOnOutputs), name="value_valid"),
        CheckConstraint("noted_by_origin = 'operator'", name="operator_only"),
        CheckConstraint("length(noted_by) > 0", name="noted_by_present"),
        CheckConstraint("length(model_id) > 0", name="model_id_present"),
        Index("ix_dw_model_terms_notes_model", "model_id", text("noted_at DESC")),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    training_on_outputs: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    noted_by: Mapped[str] = mapped_column(Text, nullable=False)
    noted_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Where a suggested value came from (the model's Hub card), when the form was pre-filled.
    prefill_source: Mapped[str | None] = mapped_column(Text)
    noted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
