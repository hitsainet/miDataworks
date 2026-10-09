"""Detector sets and their roles (009 FR-009.1 - FR-009.13; FTDD 009 section 4.1).

A detector set binds four kinds of role to version splits: one training role, one in-distribution
test, one or more out-of-distribution evaluations and one calibration-negatives role. "One" is
enforced by the DATABASE with a partial unique index (``uq_dw_detector_set_roles_single``), so a
second training role fails at insert whatever the service does (FR-009.2; FTASKS 2.4).

Deviation from FTDD 009 section 4.1, recorded in the controls review: ``role`` is a CHECK-constrained
string, not a native ``dw_detector_role`` enum, following every other table here (``models/job.py``
records why: extending a native enum needs a non-transactional ``ALTER TYPE``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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

#: 008's ``detector_role`` values (008 FTDD section 4.1; FR-009.2).
DETECTOR_ROLES: tuple[str, ...] = ("train", "id_test", "ood_eval", "calibration_negatives")
#: Roles that appear exactly once in a set; ``ood_eval`` may repeat.
SINGLE_ROLES: tuple[str, ...] = ("train", "id_test", "calibration_negatives")
MAPPING_TARGETS: tuple[str, ...] = ("positive", "negative", "excluded")

_ORIGIN = "IN ('operator', 'agent')"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class DetectorSet(Base):
    __tablename__ = "dw_detector_sets"
    __table_args__ = (
        CheckConstraint("name ~ '^[a-z0-9][a-z0-9-]{0,99}$'", name="name_pattern"),
        CheckConstraint(f"created_by_origin {_ORIGIN}", name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    positive_meaning: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: ``{kind: "role", role_id}`` or ``{kind: "version", version_id, split, column}`` (FR-009.8).
    monitored_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


#: A calibration-negatives role carries a basis OBJECT (``jsonb_typeof``: SQLAlchemy writes Python
#: None as JSON ``null``, which ``IS NOT NULL`` accepts) of a known kind; a human-labelled basis
#: names its label column (a string) and its negative values (an array). Migration 0019.
#: Every term is COALESCEd: a missing key yields SQL NULL, and a CHECK that evaluates to NULL
#: PASSES, so ``->>'kind' IN (...)`` alone would admit a basis with no kind at all.
CALIBRATION_BASIS_CHECK = (
    "role <> 'calibration_negatives' OR ("
    "COALESCE(jsonb_typeof(negatives_basis), '') = 'object' "
    "AND COALESCE(negatives_basis->>'kind', '') "
    "IN ('labeler_filtered', 'assumed_negative', 'human_labelled') "
    "AND (COALESCE(negatives_basis->>'kind', '') <> 'human_labelled' OR ("
    "COALESCE(jsonb_typeof(negatives_basis->'label_column'), '') = 'string' "
    "AND COALESCE(jsonb_typeof(negatives_basis->'negative_values'), '') = 'array')))"
)


class DetectorSetRole(Base):
    __tablename__ = "dw_detector_set_roles"
    __table_args__ = (
        CheckConstraint(_in("role", DETECTOR_ROLES), name="role_valid"),
        CheckConstraint(CALIBRATION_BASIS_CHECK, name="calibration_has_basis"),
        CheckConstraint("jsonb_typeof(label_source_columns) = 'array'", name="label_sources_array"),
        CheckConstraint("length(split) > 0", name="split_present"),
        CheckConstraint("length(input_column) > 0", name="input_column_present"),
        CheckConstraint("length(label_column) > 0", name="label_column_present"),
        Index(
            "uq_dw_detector_set_roles_single",
            "set_id",
            "role",
            unique=True,
            postgresql_where=text("role <> 'ood_eval'"),
        ),
        # 002's delete refusal reads by version (FTID 009 section 4).
        Index("ix_dw_detector_set_roles_version", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    set_id: Mapped[str] = mapped_column(
        String(40), ForeignKey("dw_detector_sets.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    #: OOD order; the first-bound OOD role is the monitored default (T-45).
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_versions.id", ondelete="RESTRICT"), nullable=False
    )
    split: Mapped[str] = mapped_column(Text, nullable=False)
    input_column: Mapped[str] = mapped_column(Text, nullable=False)
    label_column: Mapped[str] = mapped_column(Text, nullable=False)
    #: raw label value (as a string) -> positive | negative | excluded.
    label_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    pair_column: Mapped[str | None] = mapped_column(Text)
    #: Columns the label was computed from; D-3 excludes them from the shortcut audit and reports
    #: them (a JSON array; ``jsonb_typeof`` so a JSON ``null`` cannot pass for "none declared").
    label_source_columns: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: ``{kind: "labeler_filtered", labeler_identity_hash, rule}``, ``{kind: "assumed_negative"}`` or
    #: ``{kind: "human_labelled", label_column, negative_values, labelled_by, rule}``.
    #: The CHECK reads ``jsonb_typeof``: SQLAlchemy writes Python None as JSON ``null``, which
    #: ``IS NOT NULL`` would accept (found by test_calibration_negatives_need_a_basis).
    negatives_basis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    display_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
