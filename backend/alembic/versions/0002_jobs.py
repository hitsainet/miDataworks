"""dw_jobs: one record for every long job (Foundation task 5.1).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dw_jobs',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('progress', sa.Float(), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_by', sa.Text(), nullable=False),
    sa.Column('started_by_origin', sa.String(length=16), nullable=False),
    sa.Column('celery_task_id', sa.String(length=255), nullable=True),
    sa.Column('required_model_id', sa.String(length=255), nullable=True),
    sa.Column('queue_reason', sa.Text(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("started_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_jobs_origin_valid')),
    sa.CheckConstraint("status IN ('queued', 'running', 'cancelling', 'cancelled', 'completed', 'failed')", name=op.f('ck_dw_jobs_status_valid')),
    sa.CheckConstraint('length(started_by) > 0', name=op.f('ck_dw_jobs_started_by_present')),
    sa.CheckConstraint('progress >= 0 AND progress <= 100', name=op.f('ck_dw_jobs_progress_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_jobs'))
    )
    op.create_index(op.f('ix_dw_jobs_kind'), 'dw_jobs', ['kind'], unique=False)
    op.create_index(op.f('ix_dw_jobs_required_model_id'), 'dw_jobs', ['required_model_id'], unique=False)
    op.create_index('ix_dw_jobs_status_kind', 'dw_jobs', ['status', 'kind'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_dw_jobs_status_kind', table_name='dw_jobs')
    op.drop_index(op.f('ix_dw_jobs_required_model_id'), table_name='dw_jobs')
    op.drop_index(op.f('ix_dw_jobs_kind'), table_name='dw_jobs')
    op.drop_table('dw_jobs')
