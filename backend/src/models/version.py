"""Versions and their satellites (FR-002.2–002.9, 002.31, 002.34, 002.37, 002.46; FTDD 002 §4.2).

A ``dw_versions`` row exists only after a build succeeded, and is immutable: a trigger (migration
``0008``) rejects every update except the tombstone (``completed -> deleted`` with the four
``deleted_*`` columns) and every physical delete. Facts recorded later about a version —
publishes, detector-set roles, reviews — live in their owners' tables (FR-002.3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
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
from .enums import ActorOrigin, InputKind, VerificationResult, VersionState, check_in

#: Columns the tombstone may set; every other column is guarded by the immutability trigger.
TOMBSTONE_COLUMNS: tuple[str, ...] = (
    "state",
    "deleted_by",
    "deleted_by_origin",
    "deleted_at",
    "delete_reason",
)


class Version(Base):
    __tablename__ = "dw_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "number", name="uq_dw_versions_dataset_number"),
        CheckConstraint(check_in("state", VersionState), name="state_valid"),
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("seed >= 0 AND seed < 2147483648", name="seed_range"),
        CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name="request_digest_pattern"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name="manifest_sha256_pattern"),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint(
            "(state = 'completed' AND deleted_at IS NULL AND deleted_by IS NULL) OR "
            "(state = 'deleted' AND deleted_at IS NOT NULL AND deleted_by IS NOT NULL "
            "AND deleted_by_origin IS NOT NULL)",
            name="tombstone_consistent",
        ),
        Index(
            "uq_dw_versions_request_digest_completed",
            "request_digest",
            unique=True,
            postgresql_where=text("state = 'completed'"),
        ),
        Index("ix_dw_versions_parent", "parent_version_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The canonical input list, mirrored relationally in ``dw_version_inputs``.
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    recipe_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("dw_recipe_bodies.hash", ondelete="RESTRICT"), nullable=False
    )
    recipe_revision_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("dw_recipe_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rowkey_scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    column_roles: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    splits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    total_rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    held_out_origin_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    drop_summary: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    manifest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    build_job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_by: Mapped[str | None] = mapped_column(Text)
    deleted_by_origin: Mapped[str | None] = mapped_column(String(16))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delete_reason: Mapped[str | None] = mapped_column(Text)


class VersionInput(Base):
    __tablename__ = "dw_version_inputs"
    __table_args__ = (
        CheckConstraint(check_in("kind", InputKind), name="kind_valid"),
        CheckConstraint(
            "(kind = 'source' AND source_id IS NOT NULL AND input_version_id IS NULL) OR "
            "(kind = 'version' AND input_version_id IS NOT NULL AND source_id IS NULL)",
            name="exactly_one_reference",
        ),
        Index("ix_dw_version_inputs_input_version", "input_version_id"),
        Index("ix_dw_version_inputs_source", "source_id"),
    )

    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    #: ``ON DELETE RESTRICT``: feature 001 cannot delete a source a version read.
    source_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_sources.id", ondelete="RESTRICT")
    )
    input_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )


class VersionStep(Base):
    __tablename__ = "dw_version_steps"
    __table_args__ = (Index("ix_dw_version_steps_execution", "step_execution_id"),)

    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    step_index: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    step_execution_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("dw_step_executions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reused: Mapped[bool] = mapped_column(Boolean, nullable=False)


class VersionBuild(Base):
    """One per build job: the canonical request and where the build has got to."""

    __tablename__ = "dw_version_builds"
    __table_args__ = (
        Index("ix_dw_version_builds_digest", "request_digest"),
        Index("ix_dw_version_builds_waiting", "waiting_execution_id"),
    )

    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), primary_key=True
    )
    dataset_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The resolved, canonical request (inputs, revision, seed, bindings, roles).
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: ``verify`` jobs rebuild with reuse disabled (FTID 7.4).
    reuse_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The version a verify job checks; null for a normal build.
    verify_version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    sources_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: ``[{index, execution_id, reused}]`` as the build links steps.
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Set while this build waits on an execution another build (or its own dispatch) computes.
    waiting_execution_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_step_executions.id", ondelete="SET NULL")
    )
    version_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VersionVerification(Base):
    __tablename__ = "dw_version_verifications"
    __table_args__ = (
        CheckConstraint(check_in("result", VerificationResult), name="result_valid"),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        Index("ix_dw_version_verifications_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    splits: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    first_mismatch: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VersionComparison(Base):
    """Cache of comparison reports; both versions are immutable, so never invalidated."""

    __tablename__ = "dw_version_comparisons"

    version_a: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    version_b: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
