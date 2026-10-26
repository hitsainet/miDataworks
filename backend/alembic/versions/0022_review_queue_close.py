"""Review queues can be closed by hand (suite tracker 4.1, operator decision D2).

Suite tracker 4.1 (operator decision D2, 2026-10-16): ``POST /api/v1/review-queues/{id}/close``
moves a queue ``open`` -> ``closed``. Until now only an audit's completion or supersession closed a
queue, so miForge could not measure a live candidate post without leaving a reviewer a permanent
queue of synthetic rows (miForge F10 8.6). The close records when, who, by which origin and why:

- ``closed_at``, ``closed_by``, ``closed_by_origin`` (``operator``/``agent``), ``close_reason``;
- CHECK ``closed_iff_closed_at``: ``state = 'closed'`` exactly when ``closed_at`` is set. The rows an
  audit closed before this migration are back-filled with ``closed_at = created_at`` and the reason
  "closed by its audit (before close was recorded)", since the true time was never stored;
- CHECK ``closed_by_pair``: ``closed_by`` and ``closed_by_origin`` are both set or both absent (an
  audit closes a queue without a person: both absent, the reason says which audit event).

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUEUES = "dw_review_queues"
BACKFILL_REASON = "closed by its audit (before close was recorded)"


def upgrade() -> None:
    op.add_column(QUEUES, sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.add_column(QUEUES, sa.Column("closed_by", sa.Text()))
    op.add_column(QUEUES, sa.Column("closed_by_origin", sa.String(16)))
    op.add_column(QUEUES, sa.Column("close_reason", sa.Text()))
    op.execute(
        sa.text(
            f"UPDATE {QUEUES} SET closed_at = created_at, close_reason = :reason "
            "WHERE state = 'closed'"
        ).bindparams(reason=BACKFILL_REASON)
    )
    op.create_check_constraint(
        op.f("ck_dw_review_queues_closed_iff_closed_at"),
        QUEUES,
        "(state = 'closed') = (closed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_dw_review_queues_closed_by_pair"),
        QUEUES,
        "(closed_by IS NULL) = (closed_by_origin IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_dw_review_queues_closed_by_origin_valid"),
        QUEUES,
        "closed_by_origin IS NULL OR closed_by_origin IN ('operator', 'agent')",
    )
    op.create_index("ix_dw_review_queues_state", QUEUES, ["state"])


def downgrade() -> None:
    op.drop_index("ix_dw_review_queues_state", table_name=QUEUES)
    for name in ("closed_by_origin_valid", "closed_by_pair", "closed_iff_closed_at"):
        op.drop_constraint(op.f(f"ck_dw_review_queues_{name}"), QUEUES, type_="check")
    for column in ("close_reason", "closed_by_origin", "closed_by", "closed_at"):
        op.drop_column(QUEUES, column)
