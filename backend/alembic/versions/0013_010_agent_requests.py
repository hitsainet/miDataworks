"""dw_agent_requests: feature 010's agent activity record (010 FTDD section 4.2; T-51).

Renumbered 0012 -> 0013 on the rebase onto feature 005 (whose 0012 adds labeling); feature 004 is
in flight and the orchestrator renumbers at merge. ``dw_approvals`` already carries 010 FTDD section 4.1's columns (Foundation 0004) and
``dw_agent_label_rows`` landed in 0009, so this is the only schema 010 adds.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dw_agent_requests",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(identity) > 0", name=op.f("ck_dw_agent_requests_identity_present")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_agent_requests")),
    )
    op.create_index(
        "ix_dw_agent_requests_created_at", "dw_agent_requests", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_dw_agent_requests_created_at", table_name="dw_agent_requests")
    op.drop_table("dw_agent_requests")
