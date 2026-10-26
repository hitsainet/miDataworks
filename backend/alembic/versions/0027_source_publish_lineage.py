"""A source imported from a miDataworks publish records that publish (operator decision D53,
2026-10-18).

``dw_sources.lineage``: ``{"kind": "midataworks_publish", "repo_id", "commit", "version_id",
"dataset", "number", "manifest_sha256"}`` when the Hugging Face import found a
``midataworks-dataset-version.json`` manifest at the pinned commit (the publish's own
``_dw_row_key``, ``_dw_occurrence`` and ``_dw_origin`` columns are then dropped and the rows are
re-keyed as usual). NULL for every other source, and for every source imported before this
migration.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dw_sources", sa.Column("lineage", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("dw_sources", "lineage")
