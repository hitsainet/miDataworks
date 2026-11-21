"""A publish names its target: a dataset, or a miForge set (suite tracker 4.4, operator decision D7).

FR-008.68 (T-40): miForge receives a set from a PRIVATE Hub repository, through the same publish
path as any other version. Until now every publish wrote ``target.kind = "dataset"`` into its
manifest, so a miForge set could be exported locally but never delivered (008 FTASKS 12.4).

- ``target_kind`` (``dataset`` | ``miforge_set``, default ``dataset`` for every existing row);
- ``miforge_set_kind``, set exactly when the target is a miForge set;
- CHECK ``miforge_set_private``: a miForge set is published privately only (FR-008.68).

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_publishes"
KINDS = "('prompt_set', 'corpus', 'preference_pairs', 'test_set', 'retention_set')"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "target_kind", sa.String(16), nullable=False, server_default=sa.text("'dataset'")
        ),
    )
    op.add_column(TABLE, sa.Column("miforge_set_kind", sa.String(24)))
    op.create_check_constraint(
        op.f("ck_dw_publishes_target_kind_valid"),
        TABLE,
        "target_kind IN ('dataset', 'miforge_set')",
    )
    op.create_check_constraint(
        op.f("ck_dw_publishes_miforge_set_kind_iff"),
        TABLE,
        "(target_kind = 'miforge_set') = (miforge_set_kind IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_dw_publishes_miforge_set_kind_valid"),
        TABLE,
        f"miforge_set_kind IS NULL OR miforge_set_kind IN {KINDS}",
    )
    op.create_check_constraint(
        op.f("ck_dw_publishes_miforge_set_private"),
        TABLE,
        "target_kind <> 'miforge_set' OR requested_visibility = 'private'",
    )


def downgrade() -> None:
    for name in (
        "miforge_set_private",
        "miforge_set_kind_valid",
        "miforge_set_kind_iff",
        "target_kind_valid",
    ):
        op.drop_constraint(op.f(f"ck_dw_publishes_{name}"), TABLE, type_="check")
    op.drop_column(TABLE, "miforge_set_kind")
    op.drop_column(TABLE, "target_kind")
