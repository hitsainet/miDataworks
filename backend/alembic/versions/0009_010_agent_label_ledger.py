"""dw_agent_label_rows: feature 010's P-07 ledger, landed with 002's build gate (FR-002.50).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dw_agent_label_rows',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('identity', sa.Text(), nullable=False),
    sa.Column('run_kind', sa.String(length=32), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('rows_counted', sa.Integer(), nullable=False),
    sa.Column('admitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("run_kind IN ('label_run', 'feature_tagging', 'probe_verdict')", name=op.f('ck_dw_agent_label_rows_run_kind_valid')),
    sa.CheckConstraint('length(identity) > 0', name=op.f('ck_dw_agent_label_rows_identity_present')),
    sa.CheckConstraint('rows_counted >= 0', name=op.f('ck_dw_agent_label_rows_rows_nonnegative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_agent_label_rows'))
    )
    op.create_index('ix_dw_agent_label_rows_version_admitted', 'dw_agent_label_rows', ['version_id', 'admitted_at'], unique=False)


def downgrade() -> None:
    op.drop_index("ix_dw_agent_label_rows_version_admitted", table_name="dw_agent_label_rows")
    op.drop_table("dw_agent_label_rows")
