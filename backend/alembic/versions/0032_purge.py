"""Purge: the operator's permanent delete of an archived item (operator decision 2026-10-20).

Numbered 0032: it follows 0031 (key-to-endpoint binding, 2026-10-20), so the chain stays one head.

Two changes:

1. ``dw_purge_events``: one append-only tombstone per purge (what was removed, by whom, why, and
   the row counts and bytes). ``purged_by_origin`` is CHECKed to ``operator``: an agent purge
   cannot even be recorded.
2. The append-only and immutability triggers of the tables a purge empties are split in two: the
   UPDATE trigger is unchanged, and the DELETE trigger gains
   ``WHEN (current_setting('dw.purge', true) IS DISTINCT FROM 'on')``. Only
   ``services/purge_service.py`` sets ``dw.purge`` (``SET LOCAL``, so it ends with its
   transaction); every other DELETE is refused exactly as before. The trigger FUNCTIONS are not
   changed, and tables a purge never deletes from (publishes, publish files, check runs, review
   decisions, calibration records, sources, archive events, purge events) keep their triggers.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENTS = "dw_purge_events"
FLAG_OFF = "current_setting('dw.purge', true) IS DISTINCT FROM 'on'"

#: table -> (trigger name, trigger function) of the BEFORE UPDATE OR DELETE trigger split here.
SPLIT: dict[str, tuple[str, str]] = {
    "dw_versions": ("dw_versions_immutable", "dw_versions_immutable"),
    "dw_version_reports": ("dw_version_reports_immutable", "dw_version_reports_immutable"),
    "dw_length_profiles": ("dw_length_profiles_insert_only", "dw_append_only"),
    "dw_diversity_reports": ("dw_diversity_reports_append_only", "dw_append_only"),
    "dw_generation_pairs": ("dw_generation_pairs_append_only", "dw_append_only"),
    "dw_generation_records": ("dw_generation_records_append_only", "dw_append_only"),
    "dw_steering_snapshots": ("dw_steering_snapshots_append_only", "dw_append_only"),
    "dw_row_events": ("dw_row_events_append_only", "dw_append_only"),
    "dw_recipe_revisions": ("dw_recipe_revisions_insert_only", "dw_append_only"),
    "dw_recipe_bodies": ("dw_recipe_bodies_insert_only", "dw_append_only"),
}


def upgrade() -> None:
    op.create_table(
        EVENTS,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("entity_kind", sa.String(24), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column("purged_by", sa.Text(), nullable=False),
        sa.Column("purged_by_origin", sa.String(16), nullable=False),
        sa.Column("counts", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("bytes_removed", sa.BigInteger(), nullable=False),
        sa.Column("paths_removed", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "entity_kind IN ('dataset', 'label_run', 'generation_run', 'approval')",
            name=op.f(f"ck_{EVENTS}_entity_kind_valid"),
        ),
        sa.CheckConstraint(
            "purged_by_origin = 'operator' AND length(purged_by) > 0",
            name=op.f(f"ck_{EVENTS}_operator_only"),
        ),
        sa.CheckConstraint(
            "bytes_removed >= 0 AND paths_removed >= 0", name=op.f(f"ck_{EVENTS}_sizes_valid")
        ),
    )
    op.create_index("ix_dw_purge_events_entity", EVENTS, ["entity_kind", "entity_id"])
    op.execute(
        f"CREATE TRIGGER {EVENTS}_append_only BEFORE UPDATE OR DELETE ON {EVENTS} "
        "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
    )
    for table, (trigger, function) in SPLIT.items():
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        op.execute(
            f"CREATE TRIGGER {trigger}_delete BEFORE DELETE ON {table} "
            f"FOR EACH ROW WHEN ({FLAG_OFF}) EXECUTE FUNCTION {function}()"
        )


def downgrade() -> None:
    for table, (trigger, function) in SPLIT.items():
        op.execute(f"DROP TRIGGER {trigger}_delete ON {table}")
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    op.execute(f"DROP TRIGGER {EVENTS}_append_only ON {EVENTS}")
    op.drop_index("ix_dw_purge_events_entity", table_name=EVENTS)
    op.drop_table(EVENTS)
