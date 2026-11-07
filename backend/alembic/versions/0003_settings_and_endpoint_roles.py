"""dw_app_settings and dw_endpoint_roles (Foundation tasks 8.1, 8.2).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dw_app_settings',
    sa.Column('key', sa.String(length=128), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('is_sensitive', sa.Boolean(), nullable=False),
    sa.Column('category', sa.String(length=50), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_dw_app_settings'))
    )
    op.create_index(op.f('ix_dw_app_settings_category'), 'dw_app_settings', ['category'], unique=False)
    op.create_table('dw_endpoint_roles',
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('protocol', sa.String(length=64), nullable=True),
    sa.Column('base_url', sa.Text(), nullable=True),
    sa.Column('model_id', sa.String(length=255), nullable=True),
    sa.Column('api_key_ciphertext', sa.Text(), nullable=True),
    sa.Column('inherit_from_judge', sa.Boolean(), nullable=False),
    sa.Column('use_mode', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("inherit_from_judge = false OR role IN ('generation', 'embeddings')", name=op.f('ck_dw_endpoint_roles_inherit_only_generation_embeddings')),
    sa.CheckConstraint("role IN ('classifier', 'judge', 'generation', 'embeddings')", name=op.f('ck_dw_endpoint_roles_role_valid')),
    sa.CheckConstraint("use_mode IN ('own', 'same_as_classifier', 'none')", name=op.f('ck_dw_endpoint_roles_use_mode_valid')),
    sa.PrimaryKeyConstraint('role', name=op.f('pk_dw_endpoint_roles'))
    )


def downgrade() -> None:
    op.drop_table('dw_endpoint_roles')
    op.drop_index(op.f('ix_dw_app_settings_category'), table_name='dw_app_settings')
    op.drop_table('dw_app_settings')
