"""Operator code rules (feature 018, migration ``0043``; FTDD 018 sections 5.2 - 5.5).

A code-rule VERSION is a ``dw_config_versions`` row of kind ``code_rule`` (insert-only, numbered per
name): ``body`` = ``{document, cases, code_sha256}``, ``body_canonical`` = the canonical document
text, ``body_sha256`` = ``code_sha256`` - so the database itself proves the stored bytes hash to the
value that was approved (016's ``canonical_sha`` CHECK). :class:`CodeRule` is the head row of a
name: its one behaviour key and the archive columns.

:class:`CodeRuleApproval` is the durable per-hash approval list (FR-018.29), modelled on
``dw_operator_allowlist``: append-only, ``origin`` CHECKed to ``'operator'`` (agents can never
approve, P-09), current state = the latest row per hash, READ FRESH on every use
(``services/code_rules/approvals.py``). :class:`CodeRuleContractRun` is the history of each hash's
cases under each runner contract version (FR-018.31): insert-only, never an overwritten column.
:class:`CodeRuleUse` (``0044``, slice 4) records each code rule a build step ran (FR-018.39):
insert-only, deleted only by a purge, every reference RESTRICT.
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
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from ..rules.rule_set import NAME_PATTERN
from ..schemas.behaviour import BEHAVIOUR_KEY_PATTERN
from .archive import Archivable, archived_check
from .enums import ActorOrigin, check_in

#: What a row of the approval list does.
APPROVAL_ACTIONS: tuple[str, ...] = ("approve", "revoke")
SHA256_CHECK = "~ '^[0-9a-f]{64}$'"


class CodeRule(Archivable, Base):
    """The head of a code-rule name (one behaviour key per name; FR-018.32)."""

    __tablename__ = "dw_code_rules"
    __table_args__ = (
        CheckConstraint(f"name ~ '{NAME_PATTERN}'", name="name_pattern"),
        CheckConstraint(f"behaviour_key ~ '{BEHAVIOUR_KEY_PATTERN}'", name="behaviour_key_slug"),
        archived_check(),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        Index("ix_dw_code_rules_behaviour_key", "behaviour_key"),
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
        """The archive service names an item by ``id``; a code rule's is its name."""
        return self.name


class CodeRuleApproval(Base):
    """One approve or revoke of a content hash. Insert-only; ``origin`` is always the operator."""

    __tablename__ = "dw_code_rule_approvals"
    __table_args__ = (
        CheckConstraint(f"code_sha256 {SHA256_CHECK}", name="sha256_pattern"),
        CheckConstraint("action IN ('approve', 'revoke')", name="action_valid"),
        CheckConstraint("origin = 'operator'", name="origin_operator_only"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_present"),
        CheckConstraint("length(btrim(changed_by)) > 0", name="changed_by_present"),
        Index("ix_dw_code_rule_approvals_sha_id", "code_sha256", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False, default="operator")
    #: Set when the row came from an approved agent request (``dw_approvals``).
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CodeRuleContractRun(Base):
    """A hash's cases run under one runner contract version (FR-018.31). Insert-only."""

    __tablename__ = "dw_code_rule_contract_runs"
    __table_args__ = (
        CheckConstraint(f"code_sha256 {SHA256_CHECK}", name="sha256_pattern"),
        CheckConstraint("length(contract_version) > 0", name="contract_version_present"),
        CheckConstraint(
            "passed OR failing_case IS NOT NULL OR reason IS NOT NULL", name="failure_named"
        ),
        Index(
            "ix_dw_code_rule_contract_runs_sha_contract", "code_sha256", "contract_version", "id"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    code_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(Text, nullable=False)
    bundle_digest: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failing_case: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CodeRuleUse(Base):
    """One code rule a step execution ran (FR-018.39; FTDD §5.6): the rule set that called it, the
    exact version, what the runner reported, the approve row in force at resolution and the rows
    whose code-rule signal failed (``reasons``: reason -> rows). Insert-only."""

    __tablename__ = "dw_code_rule_uses"
    __table_args__ = (
        CheckConstraint(f"code_sha256 {SHA256_CHECK}", name="sha256_pattern"),
        CheckConstraint("failed_rows >= 0", name="failed_rows_nonnegative"),
        CheckConstraint("length(runner_contract) > 0", name="runner_contract_present"),
        Index("ix_dw_code_rule_uses_code_version", "code_rule_config_version_id"),
        Index("ix_dw_code_rule_uses_sha", "code_sha256"),
    )

    step_execution_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("dw_step_executions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    code_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_set_config_version_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_config_versions.id", ondelete="RESTRICT"), nullable=False
    )
    code_rule_config_version_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_config_versions.id", ondelete="RESTRICT"), nullable=False
    )
    runner_contract: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the runner reported for the step; NULL when it never answered (every row a host fault).
    contract_version: Mapped[str | None] = mapped_column(Text)
    bundle_digest: Mapped[str | None] = mapped_column(Text)
    approval_row_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dw_code_rule_approvals.id", ondelete="RESTRICT"), nullable=False
    )
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
