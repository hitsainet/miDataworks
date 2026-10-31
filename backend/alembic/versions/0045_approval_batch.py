"""ENH-006 D79: approve every waiting settings change in one decision.

``dw_approvals.batch_id`` (nullable): set on each request the operator approved through
``POST /api/v1/approvals/approve-batch``, the same id on every request of that one decision. Each
request is still approved, executed and recorded on its own row; the id only says they were
decided together. NULL for every request approved alone, and for every request decided before.

``downgrade`` drops the column (the batch grouping is lost; every decision stays recorded).

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dw_approvals", sa.Column("batch_id", sa.String(length=40), nullable=True))
    op.create_index("ix_dw_approvals_batch_id", "dw_approvals", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_dw_approvals_batch_id", table_name="dw_approvals")
    op.drop_column("dw_approvals", "batch_id")
