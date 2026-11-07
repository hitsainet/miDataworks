"""A miStudio view registration records the manifest it was sent with (009 FTASKS 17.1 live
finding, 2026-10-20).

``dw_mistudio_registrations.manifest_sha256``: the canonical SHA-256 of the dataset-version
manifest sent with a view's registration; NULL for a view registered without one (every view made
before miStudio served ``dataset_version_manifest``). The view uniqueness index gains
``coalesce(manifest_sha256, '')``, so a re-send that registers a view afresh WITH the manifest adds
a row beside the manifest-less one instead of replacing it: the older view stays traceable (P-21).

Downgrades exist for an empty database only (FTDD 002 section 11); with a re-registered view
present, recreating the narrower index fails, which is the intended refusal.

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_mistudio_registrations"
INDEX = "uq_dw_mistudio_registrations_view"
KEY = ["mistudio_base_url", "mistudio_dataset_id", "role", "mapping_sha256", "columns_sha256"]
WHERE = sa.text("kind = 'view'")


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("manifest_sha256", sa.String(length=64)))
    op.drop_index(INDEX, table_name=TABLE, postgresql_where=WHERE)
    op.create_index(
        INDEX,
        TABLE,
        [*KEY, sa.literal_column("coalesce(manifest_sha256, '')")],
        unique=True,
        postgresql_where=WHERE,
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name=TABLE, postgresql_where=WHERE)
    op.create_index(INDEX, TABLE, KEY, unique=True, postgresql_where=WHERE)
    op.drop_column(TABLE, "manifest_sha256")
