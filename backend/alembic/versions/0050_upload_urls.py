"""G-3 (FU-7a, operator decision 2026-11-25): signed, single-use upload URLs.

``dw_upload_urls`` records each URL issued (who, file, split, byte cap, expiry) and its single
use (``used_at``, set by a conditional UPDATE when the PUT arrives), with the job and source the
upload started. A new table: no existing row changes.

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_upload_urls"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(40), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text()),
        sa.Column("csv", postgresql.JSONB()),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_sha256", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("job_id", sa.Text()),
        sa.Column("source_id", sa.Text()),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.CheckConstraint("max_bytes > 0", name=op.f(f"ck_{TABLE}_max_bytes_positive")),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')", name=op.f(f"ck_{TABLE}_origin_valid")
        ),
        sa.CheckConstraint("length(created_by) > 0", name=op.f(f"ck_{TABLE}_created_by_present")),
    )


def downgrade() -> None:
    op.drop_table(TABLE)
