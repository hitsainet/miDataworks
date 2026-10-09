"""``dw_sources``, ``dw_source_files``, ``dw_source_annotations`` (001 FTDD section 4.1; 001 FTASKS 2.2).

A source is data as it entered miDataworks, pinned and frozen. Feature 002's
``dw_version_inputs.source_id`` references ``dw_sources.id`` with ``ON DELETE RESTRICT``.

Database guards (migration ``0005``): once ``ready``, a source's identity, licence-at-import and
detection columns never change; the only transitions are ``importing -> ready | failed |
cancelled`` and ``ready -> deleted``. Files and annotations are append-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
from .source_enums import AnnotationKind, Redistribution, SourceKind, SourceState, TokenTier


class Source(Base):
    __tablename__ = "dw_sources"
    __table_args__ = (
        CheckConstraint(check_in("kind", SourceKind), name="kind_valid"),
        CheckConstraint(check_in("state", SourceState), name="state_valid"),
        CheckConstraint(
            "token_tier IS NULL OR " + check_in("token_tier", TokenTier), name="token_tier_valid"
        ),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint(
            "deleted_by_origin IS NULL OR " + check_in("deleted_by_origin", ActorOrigin),
            name="deleted_origin_valid",
        ),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint(
            "resolved_commit IS NULL OR resolved_commit ~ '^[0-9a-f]{40}$'", name="commit_pattern"
        ),
        CheckConstraint(
            "content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_pattern"
        ),
        # Kind-dependent identity: an HF source has a repository; an upload has none.
        CheckConstraint(
            "(kind = 'hf' AND repo_id IS NOT NULL AND content_hash IS NULL) OR "
            "(kind = 'upload' AND repo_id IS NULL AND resolved_commit IS NULL)",
            name="kind_identity",
        ),
        # A ready source is pinned (FR-002.7 relies on this).
        CheckConstraint(
            "state <> 'ready' OR (kind = 'hf' AND resolved_commit IS NOT NULL) OR "
            "(kind = 'upload' AND content_hash IS NOT NULL)",
            name="ready_is_pinned",
        ),
        Index(
            "uq_dw_sources_hf_identity",
            "repo_id",
            text("coalesce(config, '')"),
            text("coalesce(split_selection, '')"),
            "resolved_commit",
            unique=True,
            postgresql_where=text("kind = 'hf' AND state IN ('importing', 'ready')"),
        ),
        Index(
            "uq_dw_sources_upload_identity",
            "content_hash",
            unique=True,
            postgresql_where=text("kind = 'upload' AND state IN ('importing', 'ready')"),
        ),
        Index("ix_dw_sources_state_created", "state", text("created_at DESC")),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    repo_id: Mapped[str | None] = mapped_column(Text)
    config: Mapped[str | None] = mapped_column(Text)
    split_selection: Mapped[str | None] = mapped_column(Text)
    requested_ref: Mapped[str | None] = mapped_column(Text)
    resolved_commit: Mapped[str | None] = mapped_column(String(40))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    parse_options: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    licence_raw: Mapped[Any | None] = mapped_column(JSONB)
    licence_display: Mapped[str] = mapped_column(Text, nullable=False)
    licence_origin: Mapped[str | None] = mapped_column(String(16))
    gated: Mapped[str | None] = mapped_column(String(16))
    token_tier: Mapped[str | None] = mapped_column(String(16))
    detection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    library_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    import_job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="SET NULL")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    deleted_by: Mapped[str | None] = mapped_column(Text)
    deleted_by_origin: Mapped[str | None] = mapped_column(String(16))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceFile(Base):
    __tablename__ = "dw_source_files"
    __table_args__ = (
        UniqueConstraint("source_id", "split", name="uq_dw_source_files_source_split"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_pattern"),
        CheckConstraint(
            "original_sha256 IS NULL OR original_sha256 ~ '^[0-9a-f]{64}$'",
            name="original_sha256_pattern",
        ),
        CheckConstraint("rows >= 0 AND bytes >= 0", name="counts_nonnegative"),
        CheckConstraint("dw_columns_unreserved(columns)", name="no_reserved_columns"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_sources.id", ondelete="RESTRICT"), nullable=False
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)
    #: Relative to ``DATA_DIR``.
    path: Mapped[str] = mapped_column(Text, nullable=False)
    rows: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``[{name, type}]`` in file order.
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    original_name: Mapped[str | None] = mapped_column(Text)
    original_sha256: Mapped[str | None] = mapped_column(String(64))
    original_bytes: Mapped[int | None] = mapped_column(BigInteger)


class SourceAnnotation(Base):
    __tablename__ = "dw_source_annotations"
    __table_args__ = (
        CheckConstraint(check_in("kind", AnnotationKind), name="kind_valid"),
        CheckConstraint(
            "redistribution IS NULL OR " + check_in("redistribution", Redistribution),
            name="redistribution_valid",
        ),
        CheckConstraint(
            "kind = 'detection_override' OR redistribution IS NOT NULL",
            name="redistribution_required",
        ),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("length(reason) > 0", name="reason_present"),
        # S3-01 / FR-001.38: an agent annotation carries its approval; an operator's never does.
        CheckConstraint(
            "(created_by_origin = 'agent' AND approval_id IS NOT NULL AND approved_by IS NOT NULL)"
            " OR (created_by_origin = 'operator' AND approval_id IS NULL AND approved_by IS NULL)",
            name="agent_needs_approval",
        ),
        Index("ix_dw_source_annotations_source_kind", "source_id", "kind", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_sources.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    redistribution: Mapped[str | None] = mapped_column(String(16))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
