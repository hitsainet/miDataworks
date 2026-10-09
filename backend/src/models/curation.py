"""Feature 004's two tables: immutable version reports and the warning-level history (FTDD 004 §4.1).

``dw_version_reports`` holds one row per (version, kind, operator, operator version, parameter hash,
inputs digest) once completed. Versions never change (FR-002.3), so a completed report never goes
stale; a trigger (migration ``0015``) refuses any update of a completed row.

``dw_shortcut_levels`` is the append-only history of warning margins, global and per dataset. The
current level is the latest row per scope. ``origin`` is checked to equal ``'operator'``: agents may
read levels but never write them (P-09), and the check makes that a schema fact, the second wall
behind the route's 403.

Vocabularies are CHECK-constrained strings built with :func:`~src.models.enums.check_in`, not the
native PostgreSQL enums the FTID names: Foundation and features 002/003 chose CHECK strings because
extending a native enum needs a non-transactional ``ALTER TYPE``; the code wins over the design text
(recorded in ``0xcc/reviews/004_implementation_controls_2026-10-07.md``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import ActorOrigin, check_in


class ReportKind(StrEnum):
    PROFILE = "profile"
    SHORTCUT_AUDIT = "shortcut_audit"
    LEAKAGE = "leakage"
    CONTAMINATION = "contamination"
    CLUSTERS = "clusters"
    TRL_VALIDATION = "trl_validation"


class ReportState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LevelScope(StrEnum):
    GLOBAL = "global"
    DATASET = "dataset"


class LevelAction(StrEnum):
    SET = "set"
    CLEAR = "clear"


class VersionReport(Base):
    __tablename__ = "dw_version_reports"
    __table_args__ = (
        CheckConstraint(check_in("kind", ReportKind), name="kind_valid"),
        CheckConstraint(check_in("state", ReportState), name="state_valid"),
        CheckConstraint(check_in("started_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("params_hash ~ '^[0-9a-f]{64}$'", name="params_hash_pattern"),
        CheckConstraint("inputs_digest ~ '^[0-9a-f]{64}$'", name="inputs_digest_pattern"),
        CheckConstraint("length(btrim(started_by)) > 0", name="started_by_present"),
        CheckConstraint(
            "(state = 'completed') = (result IS NOT NULL AND completed_at IS NOT NULL)",
            name="completed_has_result",
        ),
        Index(
            "uq_dw_version_reports_completed",
            "version_id",
            "kind",
            "operator_name",
            "operator_version",
            "params_hash",
            "inputs_digest",
            unique=True,
            postgresql_where=text("state = 'completed'"),
        ),
        Index("ix_dw_version_reports_version_kind", "version_id", "kind"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_name: Mapped[str] = mapped_column(String(64), nullable=False)
    operator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: ``[{version_id, split?, role?}]``: a cross-role audit or leakage check names several.
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    #: SHA-256 of canonical ``inputs``, filled by the service (PostgreSQL has no canonical JSON).
    inputs_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    artefacts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    job_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT")
    )
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    started_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShortcutLevel(Base):
    __tablename__ = "dw_shortcut_levels"
    __table_args__ = (
        CheckConstraint(check_in("scope", LevelScope), name="scope_valid"),
        CheckConstraint(check_in("action", LevelAction), name="action_valid"),
        CheckConstraint("(scope = 'global') = (dataset_id IS NULL)", name="scope_dataset_coherent"),
        CheckConstraint("action = 'set' OR scope = 'dataset'", name="clear_only_for_dataset"),
        CheckConstraint(
            "(action = 'clear' AND margin_pp IS NULL) OR "
            "(action = 'set' AND margin_pp IS NOT NULL AND margin_pp >= 0 AND margin_pp < 100)",
            name="margin_range",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_present"),
        CheckConstraint("length(btrim(set_by)) > 0", name="set_by_present"),
        CheckConstraint("origin = 'operator'", name="origin_operator_only"),
        Index("ix_dw_shortcut_levels_scope", "scope", "dataset_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    dataset_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_datasets.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    margin_pp: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    set_by: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


async def _reports_never_block_delete(db: Any, version_id: str) -> None:
    """A report is derived from the version's files; it never keeps a version alive.

    A tombstoned version keeps its row, so its reports keep their foreign key; the read routes
    refuse a deleted version with ``version_deleted`` (FR-002.37).
    """
    return None


def _register_delete_checker() -> None:
    from ..services.version_delete_service import ReferenceChecker, register_reference_checker

    register_reference_checker(
        ReferenceChecker("004", "dw_version_reports", _reports_never_block_delete)
    )


_register_delete_checker()
