"""Agreement reports name the label-review queue they opened (suite tracker 1.7; 006 FTASKS 11.6).

FR-009.53 says disagreeing rows can be sent to review, and FR-006.40 builds a label-review queue
from an explicit row-key list. ``POST /agreement-reports`` with ``send_disagreements_to_review``
opened that queue but recorded it nowhere, so the report read back (``GET
/agreement-reports/{id}``) could not say a queue existed. ``review_queue_id`` (nullable, FK to
``dw_review_queues``) records it.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dw_agreement_reports",
        sa.Column(
            "review_queue_id",
            sa.String(40),
            sa.ForeignKey(
                "dw_review_queues.id",
                name=op.f("fk_dw_agreement_reports_review_queue_id_dw_review_queues"),
                ondelete="RESTRICT",
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("dw_agreement_reports", "review_queue_id")
