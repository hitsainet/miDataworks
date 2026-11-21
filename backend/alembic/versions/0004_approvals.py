"""dw_approvals (Foundation task 9.1).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dw_approvals',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target', sa.Text(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('request_digest', sa.String(length=80), nullable=False),
    sa.Column('secret_payload', sa.Text(), nullable=True),
    sa.Column('requested_by', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('result_kind', sa.String(length=32), nullable=True),
    sa.Column('result_id', sa.String(length=64), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'executing', 'executed', 'failed', 'rejected', 'expired')", name=op.f('ck_dw_approvals_status_valid')),
    sa.CheckConstraint('length(requested_by) > 0', name=op.f('ck_dw_approvals_requested_by_present')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_approvals'))
    )
    op.create_index('ix_dw_approvals_requested_by_created', 'dw_approvals', ['requested_by', 'created_at'], unique=False)
    op.create_index('ix_dw_approvals_status_expires', 'dw_approvals', ['status', 'expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_dw_approvals_status_expires', table_name='dw_approvals')
    op.drop_index('ix_dw_approvals_requested_by_created', table_name='dw_approvals')
    op.drop_table('dw_approvals')
