"""Label runs record the rows they cover beside the keys they score (2026-10-08 live finding 2).

The live check of the probe reproduction gate found the label run counting 537 while the
reproduction link and the gate counted 540 over the same split: four rows read ``"nan"``, share one
row key (002's key is the row's content, ADR-005 / T-07) and were labelled twice each way. The run
counted distinct keys, the others counted rows, and nothing said so.

``dw_label_runs.row_coverage`` (JSONB, NULL) records the plan's coverage at start: ``rows``,
``row_keys`` (= ``rows_total``), ``keys_with_copies``, ``rows_in_copied_keys`` and the version's
warning when copies' metadata disagree. NULL on every earlier run means "not recorded"; nothing is
back-filled. A CHECK keeps it an object or absent (a JSON ``null`` passes a bare type test).

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_label_runs"
CHECK = "ck_dw_label_runs_row_coverage_object"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("row_coverage", postgresql.JSONB(astext_type=sa.Text())))
    op.create_check_constraint(
        op.f(CHECK),
        TABLE,
        "row_coverage IS NULL OR COALESCE(jsonb_typeof(row_coverage), '') = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CHECK), TABLE, type_="check")
    op.drop_column(TABLE, "row_coverage")
