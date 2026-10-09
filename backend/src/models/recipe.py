"""Recipes: content-addressed bodies, named recipes, revisions, drafts (FR-002.10–002.18).

FTDD 002 section 4.2. ``dw_recipe_bodies`` and ``dw_recipe_revisions`` are insert-only (triggers
in migration ``0008``); a body's hash is re-checked against its canonical bytes on insert. Drafts
are the only mutable recipe data, and a draft has no hash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..core.database import Base
from .enums import ActorOrigin, check_in

HASH_PATTERN = "^[0-9a-f]{64}$"


class RecipeBody(Base):
    __tablename__ = "dw_recipe_bodies"
    __table_args__ = (CheckConstraint(f"hash ~ '{HASH_PATTERN}'", name="hash_pattern"),)

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: The exact bytes hashed.
    canonical: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    #: A query copy of the body.
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Recipe(Base):
    __tablename__ = "dw_recipes"
    __table_args__ = (
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint(
            "archived_by_origin IS NULL OR " + check_in("archived_by_origin", ActorOrigin),
            name="archived_origin_valid",
        ),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
        CheckConstraint("length(name) > 0", name="name_present"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(Text)
    archived_by_origin: Mapped[str | None] = mapped_column(String(16))
    head_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("dw_recipe_revisions.id", use_alter=True, ondelete="RESTRICT"),
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RecipeRevision(Base):
    __tablename__ = "dw_recipe_revisions"
    __table_args__ = (
        UniqueConstraint("recipe_id", "revision_number", name="uq_dw_recipe_revisions_number"),
        CheckConstraint("revision_number >= 1", name="number_positive"),
        CheckConstraint(check_in("created_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(created_by) > 0", name="created_by_present"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_recipes.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    recipe_hash: Mapped[str] = mapped_column(
        String(64), ForeignKey("dw_recipe_bodies.hash", ondelete="RESTRICT"), nullable=False
    )
    #: Labels outside the hashed body, one per step (or empty).
    step_labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    cloned_from_revision_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_recipe_revisions.id", ondelete="RESTRICT")
    )
    imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecipeDraft(Base):
    __tablename__ = "dw_recipe_drafts"
    __table_args__ = (
        CheckConstraint(check_in("updated_by_origin", ActorOrigin), name="origin_valid"),
        CheckConstraint("length(updated_by) > 0", name="updated_by_present"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    recipe_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_recipes.id", ondelete="SET NULL")
    )
    dataset_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("dw_datasets.id", ondelete="SET NULL")
    )
    name: Mapped[str | None] = mapped_column(String(100))
    #: May be invalid; validated only when saved as a revision.
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    step_labels: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: The guided flow's state: ``{step, choices}`` (FR-002.48).
    flow_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_by: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
