"""ENH-006 D116 G7: a detector set may hold extra evaluation views.

``dw_detector_set_roles.role`` gains ``extra_eval``, and the partial unique index that keeps a role to one
per set leaves it out as it leaves out ``ood_eval``: zero or more of each. No existing row changes.

``downgrade`` deletes the ``extra_eval`` rows, because the older constraint cannot hold them. The versions
they bound and the sends that snapshotted them stay.

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_detector_set_roles"
CHECK = "ck_dw_detector_set_roles_role_valid"
INDEX = "uq_dw_detector_set_roles_single"
OLD_ROLES = "role IN ('train', 'id_test', 'ood_eval', 'calibration_negatives')"
NEW_ROLES = "role IN ('train', 'id_test', 'ood_eval', 'calibration_negatives', 'extra_eval')"


def upgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK), TABLE, NEW_ROLES)
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(
        INDEX,
        TABLE,
        ["set_id", "role"],
        unique=True,
        postgresql_where=sa.text("role NOT IN ('ood_eval', 'extra_eval')"),
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {TABLE} WHERE role = 'extra_eval'")  # noqa: S608 - a constant
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(
        INDEX,
        TABLE,
        ["set_id", "role"],
        unique=True,
        postgresql_where=sa.text("role <> 'ood_eval'"),
    )
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.create_check_constraint(op.f(CHECK), TABLE, OLD_ROLES)
