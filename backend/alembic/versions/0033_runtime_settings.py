"""Runtime environment settings: the change history and one namespace key (feature 012).

Numbered 0033: it follows 0032 (purge, 2026-10-20), so the chain stays one head.

1. ``dw_setting_changes``: one append-only row per non-secret settings write or reset (ENH-003
   D12): key, previous and new stored value, actor and origin, approval id, optional reason.
   UPDATE and DELETE are refused by ``dw_append_only()`` (migration 0005).
2. A stored ``detector_hub_namespace`` becomes ``publish_default_namespace`` (012 FTID I2): the two
   keys controlled one value, and overrides are keyed by the ``Settings`` field name. The row is
   moved only when no ``publish_default_namespace`` row exists; the old key is removed either way.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHANGES = "dw_setting_changes"


def upgrade() -> None:
    op.create_table(
        CHANGES,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("previous_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("approval_id", sa.String(64)),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "origin IN ('operator', 'agent') AND length(actor) > 0",
            name=op.f(f"ck_{CHANGES}_actor_valid"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) <= 500", name=op.f(f"ck_{CHANGES}_reason_length")
        ),
    )
    op.create_index("ix_dw_setting_changes_key", CHANGES, ["key", "changed_at"])
    op.create_index("ix_dw_setting_changes_changed_at", CHANGES, ["changed_at"])
    op.execute(
        f"CREATE TRIGGER {CHANGES}_append_only BEFORE UPDATE OR DELETE ON {CHANGES} "
        "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
    )
    op.execute(
        "INSERT INTO dw_app_settings (key, value, is_sensitive, category, created_at, updated_at) "
        "SELECT 'publish_default_namespace', value, false, 'environment', created_at, updated_at "
        "FROM dw_app_settings WHERE key = 'detector_hub_namespace' "
        "AND NOT EXISTS (SELECT 1 FROM dw_app_settings WHERE key = 'publish_default_namespace')"
    )
    op.execute("DELETE FROM dw_app_settings WHERE key = 'detector_hub_namespace'")


def downgrade() -> None:
    op.execute(
        "INSERT INTO dw_app_settings (key, value, is_sensitive, category, created_at, updated_at) "
        "SELECT 'detector_hub_namespace', value, false, 'publishing', created_at, updated_at "
        "FROM dw_app_settings WHERE key = 'publish_default_namespace' "
        "AND NOT EXISTS (SELECT 1 FROM dw_app_settings WHERE key = 'detector_hub_namespace')"
    )
    op.execute("DELETE FROM dw_app_settings WHERE key = 'publish_default_namespace'")
    op.execute(f"DROP TRIGGER IF EXISTS {CHANGES}_append_only ON {CHANGES}")
    op.drop_index("ix_dw_setting_changes_changed_at", table_name=CHANGES)
    op.drop_index("ix_dw_setting_changes_key", table_name=CHANGES)
    op.drop_table(CHANGES)
