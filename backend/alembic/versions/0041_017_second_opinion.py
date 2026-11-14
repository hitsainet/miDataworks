"""Feature 017 slice 6 (017-M3): second-opinion labelling (FTID 017 §4.1; TD-017.23 – TD-017.26,
TD-017.44; FR-017.38 – FR-017.44).

- ``dw_label_runs``: ``target_column TEXT`` (the column the run is compared with: the label or one
  ``sq_*``, never ``deployed_truth``), ``handoff_id VARCHAR(40)`` (one run per question over one
  selection, grouped) with an index, ``cost_basis JSONB`` (the price, questions, rows and estimate
  the run was planned or approved with; an object or absent) and ``stop_reason VARCHAR(32)``
  (``cap_reached``, ``endpoint_failed``, ``cancelled``; coverage is computed on read).
- ``dw_review_queues.handoff_id VARCHAR(40)`` with an index: the queues one hand-off opened.
- ``dw_endpoint_roles.price_per_call_usd NUMERIC(18,6)``: the operator's price per call (one row,
  one question), never negative. NULL = not priced (a hosted protocol is then treated as paid and
  refused until a price, zero included, is entered).

Every column is nullable and nothing is back-filled: a run started before 0041 has no target,
hand-off, cost basis or stop reason recorded, and none is invented. Downgrades exist for an empty
database only (FTDD 002 section 11): it refuses while a priced role or a run with a cost basis
exists, since dropping them would forget what was paid for.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNS = "dw_label_runs"
QUEUES = "dw_review_queues"
ROLES = "dw_endpoint_roles"
STOP_REASONS = ("cap_reached", "endpoint_failed", "cancelled")
STOP_CHECK = "stop_reason IS NULL OR stop_reason IN ({})".format(
    ", ".join(f"'{r}'" for r in STOP_REASONS)
)
COST_CHECK = "cost_basis IS NULL OR COALESCE(jsonb_typeof(cost_basis), '') = 'object'"
PRICE_CHECK = "price_per_call_usd IS NULL OR price_per_call_usd >= 0"


def upgrade() -> None:
    op.add_column(RUNS, sa.Column("target_column", sa.Text()))
    op.add_column(RUNS, sa.Column("handoff_id", sa.String(40)))
    op.add_column(RUNS, sa.Column("cost_basis", postgresql.JSONB(astext_type=sa.Text())))
    op.add_column(RUNS, sa.Column("stop_reason", sa.String(32)))
    op.create_check_constraint(op.f(f"ck_{RUNS}_stop_reason_valid"), RUNS, STOP_CHECK)
    op.create_check_constraint(op.f(f"ck_{RUNS}_cost_basis_object"), RUNS, COST_CHECK)
    op.create_index(op.f(f"ix_{RUNS}_handoff_id"), RUNS, ["handoff_id"])
    op.add_column(QUEUES, sa.Column("handoff_id", sa.String(40)))
    op.create_index(op.f(f"ix_{QUEUES}_handoff_id"), QUEUES, ["handoff_id"])
    op.add_column(ROLES, sa.Column("price_per_call_usd", sa.Numeric(18, 6)))
    op.create_check_constraint(op.f(f"ck_{ROLES}_price_nonnegative"), ROLES, PRICE_CHECK)


def downgrade() -> None:
    bind = op.get_bind()
    priced = bind.execute(
        sa.text(f"SELECT count(*) FROM {ROLES} WHERE price_per_call_usd IS NOT NULL")
    ).scalar_one()
    costed = bind.execute(
        sa.text(f"SELECT count(*) FROM {RUNS} WHERE cost_basis IS NOT NULL")
    ).scalar_one()
    if priced or costed:
        raise RuntimeError(
            f"{priced} priced endpoint role(s) and {costed} label run(s) with a cost basis "
            "exist; dropping 0041 would forget what was paid for."
        )
    op.drop_constraint(op.f(f"ck_{ROLES}_price_nonnegative"), ROLES, type_="check")
    op.drop_column(ROLES, "price_per_call_usd")
    op.drop_index(op.f(f"ix_{QUEUES}_handoff_id"), table_name=QUEUES)
    op.drop_column(QUEUES, "handoff_id")
    op.drop_index(op.f(f"ix_{RUNS}_handoff_id"), table_name=RUNS)
    op.drop_constraint(op.f(f"ck_{RUNS}_cost_basis_object"), RUNS, type_="check")
    op.drop_constraint(op.f(f"ck_{RUNS}_stop_reason_valid"), RUNS, type_="check")
    for column in ("stop_reason", "cost_basis", "handoff_id", "target_column"):
        op.drop_column(RUNS, column)
