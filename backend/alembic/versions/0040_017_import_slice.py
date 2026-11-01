"""Feature 017 slice 4 (017-M2): Hub import slices (FTID 017 §4.1; TD-017.20; FR-017.31).

``dw_sources.import_slice JSONB NULL``: ``{"max_rows": int?, "data_files": [str]?}``, the first
``max_rows`` rows of the named data files at the pinned commit. NULL = the whole dataset (every
existing source; nothing is back-filled). It is part of the identical-import match: the partial
unique index ``uq_dw_sources_hf_identity`` gains it, so two different slices of one dataset are two
sources and the same slice twice is one. A slice is an object, on Hub sources only
(``import_slice_shape``).

Downgrades exist for an empty database only (FTDD 002 section 11): it refuses while a sliced source
is live, since the narrower index would see two slices of one dataset as one identity.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "dw_sources"
INDEX = "uq_dw_sources_hf_identity"
LIVE_HF = "kind = 'hf' AND state IN ('importing', 'ready')"
SHAPE = (
    "import_slice IS NULL OR (kind = 'hf' AND "
    "COALESCE(jsonb_typeof(import_slice), '') = 'object')"
)


def _identity(with_slice: bool) -> None:
    columns = [
        "repo_id",
        sa.text("coalesce(config, '')"),
        sa.text("coalesce(split_selection, '')"),
        "resolved_commit",
    ]
    if with_slice:
        columns.append(sa.text("coalesce(import_slice::text, '')"))
    op.create_index(INDEX, TABLE, columns, unique=True, postgresql_where=sa.text(LIVE_HF))


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("import_slice", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.create_check_constraint(op.f(f"ck_{TABLE}_import_slice_shape"), TABLE, SHAPE)
    op.drop_index(INDEX, table_name=TABLE)
    _identity(with_slice=True)


def downgrade() -> None:
    found = (
        op.get_bind()
        .execute(
            sa.text(f"SELECT count(*) FROM {TABLE} WHERE import_slice IS NOT NULL AND {LIVE_HF}")
        )
        .scalar_one()
    )
    if found:
        raise RuntimeError(
            f"{found} live sliced Hub source(s) exist; the identity without the slice would "
            "merge them."
        )
    op.drop_index(INDEX, table_name=TABLE)
    _identity(with_slice=False)
    op.drop_constraint(op.f(f"ck_{TABLE}_import_slice_shape"), TABLE, type_="check")
    op.drop_column(TABLE, "import_slice")
