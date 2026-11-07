"""Feature 009: detector-set check fixes found on production (2026-10-07).

- ``dw_detector_set_roles.label_source_columns``: the columns a role's label was COMPUTED FROM
  (Humicroedit's ``meanGrade`` and ``grades``). D-3 passes them to 004's shortcut audit, which
  excludes them and reports them, instead of refusing a send on the label's own definition.
  A JSON array, never JSON ``null`` (``jsonb_typeof``, as the basis CHECK learned in 0016).
- The calibration-negatives basis CHECK gains kind ``human_labelled`` (people labelled the
  negatives, as Humicroedit's graders did) and now names the allowed kinds; a human-labelled basis
  must carry its label column (string) and negative values (array). It keeps 0016's
  ``jsonb_typeof(...) = 'object'`` guard against a JSON ``null`` basis.

Downgrade refuses while a role records a ``human_labelled`` basis: the old CHECK would accept it
as an object, but no code before 0019 can read it.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLE = "dw_detector_set_roles"
BASIS_CHECK = "ck_dw_detector_set_roles_calibration_has_basis"
OLD_BASIS = "role <> 'calibration_negatives' OR jsonb_typeof(negatives_basis) = 'object'"
NEW_BASIS = (
    "role <> 'calibration_negatives' OR ("
    "COALESCE(jsonb_typeof(negatives_basis), '') = 'object' "
    "AND COALESCE(negatives_basis->>'kind', '') "
    "IN ('labeler_filtered', 'assumed_negative', 'human_labelled') "
    "AND (COALESCE(negatives_basis->>'kind', '') <> 'human_labelled' OR ("
    "COALESCE(jsonb_typeof(negatives_basis->'label_column'), '') = 'string' "
    "AND COALESCE(jsonb_typeof(negatives_basis->'negative_values'), '') = 'array')))"
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "label_source_columns",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_dw_detector_set_roles_label_sources_array"),
        TABLE,
        "jsonb_typeof(label_source_columns) = 'array'",
    )
    op.drop_constraint(op.f(BASIS_CHECK), TABLE, type_="check")
    op.create_check_constraint(op.f(BASIS_CHECK), TABLE, NEW_BASIS)


def downgrade() -> None:
    human = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT count(*) FROM {TABLE} "  # noqa: S608 - a constant table name
                "WHERE negatives_basis->>'kind' = 'human_labelled'"
            )
        )
        .scalar_one()
    )
    if human:
        raise RuntimeError(
            f"{human} detector-set role(s) record a human_labelled calibration basis; change them "
            "before downgrading below 0019."
        )
    op.drop_constraint(op.f(BASIS_CHECK), TABLE, type_="check")
    op.create_check_constraint(op.f(BASIS_CHECK), TABLE, OLD_BASIS)
    op.drop_constraint(op.f("ck_dw_detector_set_roles_label_sources_array"), TABLE, type_="check")
    op.drop_column(TABLE, "label_source_columns")
