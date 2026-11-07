"""An operator step's report is stored (live2 finding, 2026-10-18).

``dw_step_executions.reports``: the JSON reports the operator returned (``OperatorResult.report``,
one per batch it ran), as the executor wrote them to the step's ``meta.json``. Until now the
executor read only ``split_roles`` from a report and dropped the rest, so a report operator such as
``cluster_embedding`` (004 FTASKS 15.5) produced nothing any record kept, and ``dedup_embedding``'s
request and cache figures were lost. NULL on a step executed before this migration (not recorded).
Kept out of the version manifest: a report can differ between a build and a rebuild (cache hits),
and the manifest is what verify-rebuild compares.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dw_step_executions", sa.Column("reports", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("dw_step_executions", "reports")
