"""dw_row_events, hash-partitioned by step execution into 32 partitions (002 FTASKS 3.3; T-09).

The partitioned-table pattern follows miStudio's
``backend/alembic/versions/76918d8aa763_create_feature_discovery_tables.py`` (which uses range
partitions; a pattern only, no code was copied). Indexes are created on the parent, so every
partition inherits them. Events are append-only.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""


from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PARTITIONS = 32


def upgrade() -> None:
    op.create_table('dw_row_events',
    sa.Column('step_execution_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('row_key', sa.LargeBinary(), nullable=False),
    sa.Column('occurrence', sa.Integer(), nullable=False),
    sa.Column('new_row_key', sa.LargeBinary(), nullable=True),
    sa.Column('new_occurrence', sa.Integer(), nullable=True),
    sa.Column('related_row_key', sa.LargeBinary(), nullable=True),
    sa.Column('parent_keys', sa.ARRAY(sa.LargeBinary()), nullable=True),
    sa.Column('split', sa.String(length=64), nullable=True),
    sa.Column('reason_code', sa.String(length=64), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('statistic_name', sa.String(length=64), nullable=True),
    sa.Column('statistic_value', sa.Float(), nullable=True),
    sa.Column('statistic_text', sa.Text(), nullable=True),
    sa.Column('threshold', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint("(kind = 'changed') = (new_row_key IS NOT NULL)", name=op.f('ck_dw_row_events_new_key_only_when_changed')),
    sa.CheckConstraint("kind IN ('dropped', 'changed', 'added', 'split_assigned')", name=op.f('ck_dw_row_events_kind_valid')),
    sa.CheckConstraint("kind NOT IN ('dropped', 'changed') OR statistic_name IS NOT NULL", name=op.f('ck_dw_row_events_statistic_required')),
    sa.CheckConstraint('length(reason_code) > 0 AND length(reason) > 0', name=op.f('ck_dw_row_events_reason_present')),
    sa.CheckConstraint('octet_length(row_key) = 32', name=op.f('ck_dw_row_events_row_key_length')),
    sa.PrimaryKeyConstraint('step_execution_id', 'seq', name=op.f('pk_dw_row_events')),
    postgresql_partition_by='HASH (step_execution_id)'
    )
    op.create_index('ix_dw_row_events_exec_kind_reason', 'dw_row_events', ['step_execution_id', 'kind', 'reason_code'], unique=False)
    op.create_index('ix_dw_row_events_new_row_key', 'dw_row_events', ['new_row_key'], unique=False, postgresql_where=sa.text('new_row_key IS NOT NULL'))
    op.create_index('ix_dw_row_events_row_key', 'dw_row_events', ['row_key'], unique=False)
    for n in range(PARTITIONS):
        op.execute(
            f"CREATE TABLE dw_row_events_p{n:02d} PARTITION OF dw_row_events "
            f"FOR VALUES WITH (MODULUS {PARTITIONS}, REMAINDER {n})"
        )
    op.execute(
        "CREATE TRIGGER dw_row_events_append_only BEFORE UPDATE OR DELETE ON dw_row_events "
        "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
    )


def downgrade() -> None:
    op.drop_table("dw_row_events")  # partitions are dropped with their parent
