"""Decision templates may be for TypeSafe's JEV API (FR-005.56; operator decision D58, 2026-10-18).

``ck_dw_decision_templates_protocol_valid`` gains ``typesafe_systemone``.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_decision_templates"
NAME = "ck_dw_decision_templates_protocol_valid"


def upgrade() -> None:
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(
        op.f(NAME),
        TABLE,
        "protocol IN ('openai_scoring', 'tei_classification', 'plugin', 'typesafe_systemone')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(NAME), TABLE, type_="check")
    op.create_check_constraint(
        op.f(NAME), TABLE, "protocol IN ('openai_scoring', 'tei_classification', 'plugin')"
    )
