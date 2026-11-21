"""Rule sets (feature 016, migration ``0036``; FTDD 016 sections 4.2 - 4.4).

A rule-set VERSION is a ``dw_config_versions`` row of kind ``rule_set`` (insert-only, numbered per
name). :class:`RuleSet` is the head row of a name: its one behaviour key and the archive columns.
:class:`RuleSetUse` records what each step execution ran (FR-016.24): insert-only, with RESTRICT
keys, so a used version is never deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from ..rules.rule_set import NAME_PATTERN, USES
from ..schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from .archive import Archivable, archived_check
from .enums import ActorOrigin, check_in

USE_CHECK = "use IN (" + ", ".join(f"'{u}'" for u in USES) + ")"


class RuleSet(Archivable, Base):
    """The head of a rule-set name (one behaviour key per name; FR-016.16)."""

    __tablename__ = "dw_rule_sets"
    __table_args__ = (
        CheckConstraint(f"name ~ '{NAME_PATTERN}'", name="name_pattern"),
        CheckConstraint(f"behaviour_key ~ '{BEHAVIOUR_KEY_PATTERN}'", name="behaviour_key_slug"),
        archived_check(),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        Index("ix_dw_rule_sets_behaviour_key", "behaviour_key"),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    behaviour_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def id(self) -> str:
        """The archive and purge services name an item by ``id``; a rule set's is its name."""
        return self.name


class RuleSetUse(Base):
    """One rule-set version one step execution ran, and why (``use``). Insert-only."""

    __tablename__ = "dw_rule_set_uses"
    __table_args__ = (
        CheckConstraint("rule_set_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_pattern"),
        CheckConstraint(f"behaviour_key ~ '{BEHAVIOUR_KEY_PATTERN}'", name="behaviour_key_slug"),
        CheckConstraint(USE_CHECK, name="use_valid"),
        Index("ix_dw_rule_set_uses_config_version", "config_version_id"),
    )

    step_execution_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("dw_step_executions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    config_version_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_config_versions.id", ondelete="RESTRICT"), primary_key=True
    )
    rule_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    behaviour_key: Mapped[str] = mapped_column(Text, nullable=False)
    use: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RuleDerivation(Base):
    """One rule-derivation job's inputs, state, figures and draft (FR-016.29 - FR-016.32; 0038).

    A guard trigger refuses any UPDATE of a terminal row and any DELETE outside ``dw.purge``."""

    __tablename__ = "dw_rule_derivations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "state IS NULL OR state IN ('measured', 'low_coverage', 'not_measured')",
            name="state_valid",
        ),
        CheckConstraint("params_sha256 ~ '^[0-9a-f]{64}$'", name="params_sha256"),
        CheckConstraint(
            "(draft_canonical IS NULL AND draft_sha256 IS NULL) OR (draft_canonical IS NOT NULL "
            "AND encode(sha256(convert_to(draft_canonical, 'UTF8')), 'hex') = draft_sha256)",
            name="draft_sha",
        ),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        Index("ix_dw_rule_derivations_source", "source_version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    #: Inputs AND the settings in force at start (a later setting change never changes a result).
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    params_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    method_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str | None] = mapped_column(String(16))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    draft_canonical: Mapped[str | None] = mapped_column(Text)
    draft_sha256: Mapped[str | None] = mapped_column(String(64))
    inherited_licence_classes: Mapped[list[str] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _register_delete_checker() -> None:
    """A version a derivation is still reading is not deleted (002's reference registry)."""
    from ..services.version_delete_service import (
        LIVE,
        Reference,
        ReferenceChecker,
        register_reference_checker,
    )

    async def live_derivations(db: Any, version_id: str) -> Reference | None:
        """Live = the row is not terminal AND its job is live. Review R1 (slices 2-4): a job the
        janitor failed, or one cancelled before a worker claimed it, never writes the row's end,
        so the row alone would block the delete for ever (the read route already reads the
        job's end for the same reason)."""
        from sqlalchemy import select

        from .job import Job

        found = (
            await db.execute(
                select(RuleDerivation.id)
                .join(Job, Job.id == RuleDerivation.job_id)
                .where(
                    RuleDerivation.source_version_id == version_id,
                    RuleDerivation.status.in_(("queued", "running")),
                    Job.status.in_(LIVE),
                )
            )
        ).first()
        if found is None:
            return None
        return Reference(
            "version_in_use",
            f"Rule derivation {found[0]} is reading this version. Wait for it to finish or "
            "cancel its job, then delete.",
            {"derivation_id": found[0]},
        )

    register_reference_checker(ReferenceChecker("016", "dw_rule_derivations", live_derivations))


_register_delete_checker()
