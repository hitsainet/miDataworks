"""Feature 011: a download step records whether miStudio confirmed the commit, and a results
snapshot records how its runs were found and each role's commit (ENH-002; FR-011.7, FR-011.11).

- ``dw_detector_send_steps.commit_check``: ``verified`` (miStudio 035 recorded the publish commit),
  ``claimed`` (miStudio did not pin downloads when the step ran), NULL (written before 011). No
  backfill: NULL is the truth for every existing row, and reads "not checked (sent before feature
  011)". Written once per download step, never upgraded.
- ``dw_detector_results.provenance`` (011 FTID I1): ``{run_discovery, commits}``; NULL on a
  snapshot taken before 011, read as "not recorded". Additive; snapshots stay insert-only.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STEPS = "dw_detector_send_steps"
RESULTS = "dw_detector_results"


def upgrade() -> None:
    op.add_column(STEPS, sa.Column("commit_check", sa.String(16)))
    op.create_check_constraint(
        op.f("ck_dw_detector_send_steps_commit_check_valid"),
        STEPS,
        "commit_check IS NULL OR commit_check IN ('verified', 'claimed')",
    )
    op.add_column(RESULTS, sa.Column("provenance", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column(RESULTS, "provenance")
    op.drop_constraint(op.f("ck_dw_detector_send_steps_commit_check_valid"), STEPS, type_="check")
    op.drop_column(STEPS, "commit_check")
