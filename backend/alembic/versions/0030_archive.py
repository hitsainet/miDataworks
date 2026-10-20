"""Archive: hide test and abandoned work from default lists, keep every row (2026-10-20).

Operator decision 2026-10-20. Datasets, label runs, generation runs and approvals gain four
nullable columns, ``archived_at``, ``archived_by``, ``archived_by_origin`` and ``archive_reason``:
all NULL (shown) or the first three set (archived). Archive deletes nothing and changes no other
column, so every version, label, publish and provenance record a kept item points at still reads.
Unarchive clears the four columns.

``dw_archive_events`` is the trail: one append-only row per archive and unarchive, naming who
and why, because the columns above hold only the latest state.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("dw_datasets", "dw_label_runs", "dw_generation_runs", "dw_approvals")
CONSISTENT = (
    "(archived_at IS NULL AND archived_by IS NULL AND archived_by_origin IS NULL "
    "AND archive_reason IS NULL) OR "
    "(archived_at IS NOT NULL AND archived_by IS NOT NULL AND length(archived_by) > 0 "
    "AND archived_by_origin IN ('operator', 'agent'))"
)
EVENTS = "dw_archive_events"


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("archived_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("archived_by", sa.Text()))
        op.add_column(table, sa.Column("archived_by_origin", sa.String(16)))
        op.add_column(table, sa.Column("archive_reason", sa.Text()))
        op.create_check_constraint(op.f(f"ck_{table}_archived_consistent"), table, CONSISTENT)
    op.create_table(
        EVENTS,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("entity_kind", sa.String(24), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "entity_kind IN ('dataset', 'label_run', 'generation_run', 'approval')",
            name=op.f(f"ck_{EVENTS}_entity_kind_valid"),
        ),
        sa.CheckConstraint(
            "action IN ('archive', 'unarchive')", name=op.f(f"ck_{EVENTS}_action_valid")
        ),
        sa.CheckConstraint(
            "actor_origin IN ('operator', 'agent') AND length(actor) > 0",
            name=op.f(f"ck_{EVENTS}_actor_valid"),
        ),
    )
    op.create_index(
        "ix_dw_archive_events_entity", EVENTS, ["entity_kind", "entity_id", "created_at"]
    )
    op.execute(
        f"CREATE TRIGGER {EVENTS}_append_only BEFORE UPDATE OR DELETE ON {EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER {EVENTS}_append_only ON {EVENTS}")
    op.drop_index("ix_dw_archive_events_entity", table_name=EVENTS)
    op.drop_table(EVENTS)
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_archived_consistent"), table, type_="check")
        for column in ("archive_reason", "archived_by_origin", "archived_by", "archived_at"):
            op.drop_column(table, column)
