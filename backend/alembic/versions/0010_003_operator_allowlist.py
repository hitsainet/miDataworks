"""dw_operator_allowlist: feature 003's entry-point allowlist history (FR-003.11).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dw_operator_allowlist",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("distribution", sa.String(length=255), nullable=False),
        sa.Column("distribution_version", sa.String(length=128), nullable=False),
        sa.Column("entry_point", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.String(length=255), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('allow', 'revoke')", name=op.f("ck_dw_operator_allowlist_action_valid")
        ),
        sa.CheckConstraint(
            "origin = 'operator'", name=op.f("ck_dw_operator_allowlist_origin_operator_only")
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_dw_operator_allowlist_reason_present")
        ),
        sa.CheckConstraint(
            "length(btrim(changed_by)) > 0",
            name=op.f("ck_dw_operator_allowlist_changed_by_present"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_operator_allowlist")),
    )
    op.create_index(
        "ix_dw_operator_allowlist_triple_created",
        "dw_operator_allowlist",
        ["distribution", "distribution_version", "entry_point", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dw_operator_allowlist_triple_created", table_name="dw_operator_allowlist")
    op.drop_table("dw_operator_allowlist")
