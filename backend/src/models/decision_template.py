"""``dw_decision_templates``: versioned, hashed classifier templates (FR-005.11 – FR-005.15).

A template says how a row becomes a classifier request and how the answer becomes a probability.
It is immutable once any run has used it (FR-005.12): ``dw_label_runs.template_id`` references it
``ON DELETE RESTRICT`` and the service refuses updates (``TEMPLATE_IMMUTABLE``). A template that
names token IDs must name the model it is bound to (FR-005.13): token IDs are tokenizer-specific.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base

TEMPLATE_PROTOCOLS: tuple[str, ...] = ("openai_scoring", "tei_classification", "plugin")


class DecisionTemplate(Base):
    __tablename__ = "dw_decision_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_dw_decision_templates_name_version"),
        UniqueConstraint("content_hash", name="uq_dw_decision_templates_content_hash"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_pattern"),
        CheckConstraint(
            "protocol IN ('openai_scoring', 'tei_classification', 'plugin')", name="protocol_valid"
        ),
        CheckConstraint(
            "NOT (body ? 'verbalizer_ids') OR bound_model_id IS NOT NULL",
            name="token_ids_bound_to_model",
        ),
        CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')", name="origin_valid"
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The canonical body (FR-005.11): rendering, label set, verbalizers, constants, binding.
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    variant: Mapped[str | None] = mapped_column(String(32))
    bound_model_id: Mapped[str | None] = mapped_column(String(255))
    bound_model_revision: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
