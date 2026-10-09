"""Reproduction links (009 FR-009.77, option (b) of the operator decision of 2026-10-07).

A link says: "the rows of THIS miDataworks version split are the rows miStudio evaluated probe P on
in view V, and miStudio recorded AUROC A with interval [lo, hi] there". The reproduction gate may then
use that recorded evaluation as its target for a probe whose training data miDataworks never sent
(an imported probe), which no results snapshot can ever describe.

The claim is CHECKED when the link is made and the checks are stored with it (``checks``): row count
and class balance always; a content hash over the input texts when miStudio serves the rows. A link
whose rows provably differ is never stored. ``check_level`` is ``content`` only when the hashes were
computed and agreed, and ``counts_only`` otherwise, and the screen says which.

A link is immutable evidence: no column is ever updated (``BEFORE UPDATE`` trigger), and it may be
deleted only while no label run names it (``dw_reproduction_link_unused`` trigger, mirrored by the
service so the refusal reads well).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

CHECK_LEVELS: tuple[str, ...] = ("content", "counts_only")
LINK_ROLES: tuple[str, ...] = ("id_test", "ood_eval")


class ReproductionLink(Base):
    __tablename__ = "dw_reproduction_links"
    __table_args__ = (
        CheckConstraint(
            "check_level IN (" + ", ".join(f"'{c}'" for c in CHECK_LEVELS) + ")",
            name="check_level_valid",
        ),
        CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in LINK_ROLES) + ")", name="role_valid"
        ),
        CheckConstraint("created_by_origin IN ('operator', 'agent')", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint(
            "(created_by_origin = 'agent') = (approval_id IS NOT NULL AND approved_by IS NOT NULL)",
            name="agent_iff_approved",
        ),
        CheckConstraint("ci_low <= auroc AND auroc <= ci_high", name="interval_holds_auroc"),
        CheckConstraint("n_positive > 0 AND n_negative > 0", name="both_classes"),
        UniqueConstraint(
            "mistudio_base_url",
            "mistudio_probe_id",
            "probe_dataset_id",
            "version_id",
            "split",
            name="uq_dw_reproduction_links_target",
        ),
        Index("ix_dw_reproduction_links_probe", "mistudio_probe_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mistudio_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    #: The miStudio probe (``pm_...``) whose evaluation this is: the SOURCE of a miLLM import.
    mistudio_probe_id: Mapped[str] = mapped_column(Text, nullable=False)
    mistudio_run_id: Mapped[str | None] = mapped_column(Text)
    #: The miStudio probe dataset (view, ``pmd_...``) the evaluation scored.
    probe_dataset_id: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_id: Mapped[str | None] = mapped_column(Text)
    view_name: Mapped[str | None] = mapped_column(Text)
    #: ``id_test`` for an in-distribution view, ``ood_eval`` for out-of-distribution.
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)
    input_column: Mapped[str] = mapped_column(Text, nullable=False)
    label_column: Mapped[str] = mapped_column(Text, nullable=False)
    label_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    auroc: Mapped[float] = mapped_column(Float, nullable=False)
    ci_low: Mapped[float] = mapped_column(Float, nullable=False)
    ci_high: Mapped[float] = mapped_column(Float, nullable=False)
    n_positive: Mapped[int] = mapped_column(Integer, nullable=False)
    n_negative: Mapped[int] = mapped_column(Integer, nullable=False)
    check_level: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Every check, whether it ran, what each side said and why a check did not run.
    checks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: How miStudio scored the rows, how miLLM will, and whether the two are known to agree.
    scoring_form: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: sha256 of the probe report as read (canonical JSON), and the view record as read.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Set when an agent asked and the operator approved (``gate_target_write``): a link sets the
    #: target a gate compares against, like a calibration gate target (S3-08).
    approval_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("dw_approvals.id", ondelete="RESTRICT")
    )
    approved_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
