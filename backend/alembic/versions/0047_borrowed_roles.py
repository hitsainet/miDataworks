"""ENH-006 G21: a training-only detector set borrows a sibling set's evaluation roles.

``dw_detector_sets.borrows_from_set_id`` names the sibling (R-03.49 as amended, operator decision
2026-11-08). A foreign key with no cascade: a set that is borrowed from cannot be removed under the
set that borrows it. A CHECK keeps a set from borrowing from itself. No existing row changes.

``downgrade`` drops the column: a borrowing set then holds only its training role and D-1 refuses
it, which is what the older rule says.

Revision ID: 0047
Revises: 0046
Create Date: 2026-11-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_detector_sets"
COLUMN = "borrows_from_set_id"
FK = "fk_dw_detector_sets_borrows_from_set_id_dw_detector_sets"
CHECK = "ck_dw_detector_sets_borrow_not_self"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=40), nullable=True))
    op.create_foreign_key(op.f(FK), TABLE, TABLE, [COLUMN], ["id"])
    op.create_check_constraint(
        op.f(CHECK), TABLE, "borrows_from_set_id IS NULL OR borrows_from_set_id <> id"
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.drop_constraint(op.f(FK), TABLE, type_="foreignkey")
    op.drop_column(TABLE, COLUMN)
