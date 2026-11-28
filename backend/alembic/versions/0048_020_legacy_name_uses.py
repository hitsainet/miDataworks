"""Feature 020 (ENH-009, the suite rebrand): the old sibling environment names in use.

``dw_legacy_name_uses`` records, per old name, when any miDataworks process (API, worker, beat)
was first and last seen reading it, and how many touches; names only, never a value. P7 retires an
old name on this evidence (7 quiet days). ``dw_legacy_name_write_errors`` holds one row per
reporter whose last upsert failed (``cleared_at`` NULL until the next success), so health can say a
reading is unmeasured (CF-25 m2). ``ix_dw_mistudio_registrations_base_url`` serves the previous-address
lookup's loose index scan. No existing row changes.

``downgrade`` drops both tables (the evidence goes with them; downgrades are for an empty database).

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dw_legacy_name_uses",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("old_name", sa.String(length=64), nullable=False),
        sa.Column("new_name", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.BigInteger(), server_default="1", nullable=False),
        sa.CheckConstraint("kind IN ('env')", name=op.f("ck_dw_legacy_name_uses_kind_valid")),
        sa.PrimaryKeyConstraint("kind", "old_name", name=op.f("pk_dw_legacy_name_uses")),
    )
    op.create_table(
        "dw_legacy_name_write_errors",
        sa.Column("reporter", sa.String(length=128), nullable=False),
        sa.Column("error", sa.String(length=128), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("reporter", name=op.f("pk_dw_legacy_name_write_errors")),
    )
    # The previous-address lookup's loose index scan (020 FTASKS 6.15): the registrations' two
    # unique indexes lead with the address but are partial, so neither serves min(address).
    op.create_index(
        "ix_dw_mistudio_registrations_base_url",
        "dw_mistudio_registrations",
        ["mistudio_base_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_dw_mistudio_registrations_base_url", table_name="dw_mistudio_registrations")
    op.drop_table("dw_legacy_name_write_errors")
    op.drop_table("dw_legacy_name_uses")
