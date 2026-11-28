"""Feature 021 (suite ENH-010, weight-space programming): a version's sensitivity floor
(FR-021.18; FTDD §4.4; FTID I6).

- ``dw_versions.sensitivity TEXT``: ``sensitive`` or ``not_sensitive`` when the build's recipe,
  inputs, rule-source ancestors or bindings set it (``services/sensitivity.floor``); NULL = not
  declared. CHECK ``ck_dw_versions_sensitivity_valid``.
- ``dw_versions.sensitivity_basis JSONB``: how the floor was reached (informational).

``ADD COLUMN … NULL`` rewrites no row and fires no row trigger, so ``dw_versions_immutable``
(0008, which compares whole rows) covers both columns from the first insert. **No backfill:** the
trigger refuses an update, which is the point; an old version reads "not declared", and its
effective value is computed from lineage at read time (``services/sensitivity.effective``).

Downgrades exist for an empty database only (FTDD 002 section 11): refused while a version carries
a sensitivity.

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VERSIONS = "dw_versions"
SENSITIVITY_CHECK = "sensitivity IS NULL OR sensitivity IN ('sensitive', 'not_sensitive')"


def upgrade() -> None:
    op.add_column(VERSIONS, sa.Column("sensitivity", sa.Text()))
    op.create_check_constraint(
        op.f(f"ck_{VERSIONS}_sensitivity_valid"), VERSIONS, SENSITIVITY_CHECK
    )
    op.add_column(VERSIONS, sa.Column("sensitivity_basis", postgresql.JSONB()))


def downgrade() -> None:
    bind = op.get_bind()
    carried = bind.execute(
        sa.text(f"SELECT count(*) FROM {VERSIONS} WHERE sensitivity IS NOT NULL")  # noqa: S608
    ).scalar_one()
    if carried:
        raise RuntimeError(
            f"{carried} version(s) carry a sensitivity; a downgrade would drop that record. "
            "Downgrades are for an empty database."
        )
    op.drop_column(VERSIONS, "sensitivity_basis")
    op.drop_constraint(op.f(f"ck_{VERSIONS}_sensitivity_valid"), VERSIONS, type_="check")
    op.drop_column(VERSIONS, "sensitivity")
