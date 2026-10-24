"""Behaviour dataset support (feature 015; ENH-005 D7, D37, D38, D55-D58).

1. ``dw_detector_sets.behaviour_key`` and ``dw_rubrics.behaviour_key``: a nullable slug naming the
   behaviour miStudio's catalogue monitors (the same rule as miStudio's ``SLUG_PATTERN``; pinned
   by ``tests/unit/test_behaviour_key.py``). Not unique; indexed for the list filters. The rubric
   key is NOT part of ``content_hash`` (FTDD 015 section 4.2).
2. ``dw_mistudio_registrations.meaning_sha256``: the canonical SHA-256 of ``{behaviour,
   positive_meaning}`` as sent with a view's registration; NULL when nothing was sent. The view
   uniqueness index gains ``coalesce(meaning_sha256, '')``, so a re-registration that carries the
   meaning is a new row beside the old one (the reasoning of 0029).
3. ``dw_detector_send_steps.behaviour``: ``{"sent": bool, "reason": str}`` on register steps.
4. ``dw_sources.session_data`` / ``session_data_basis`` (``recognised`` | ``operator``): the source
   is recorded user traffic (FR-015.12). Existing sources are false: nothing could recognise them
   before. No back-fill by guessing.
5. ``dw_text_separability``: the D-9 cache (FR-015.17), keyed like ``dw_length_profiles`` plus the
   label mapping, pair column and sample size.

Downgrades exist for an empty database only (FTDD 002 section 11); with a view re-registered with a
meaning, recreating the narrower index fails, which is the intended refusal.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Copied literally (migrations never import app code); equal to ``schemas.behaviour``.
SLUG = "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
SLUG_CHECK = f"behaviour_key IS NULL OR behaviour_key ~ '{SLUG}'"

REG = "dw_mistudio_registrations"
REG_INDEX = "uq_dw_mistudio_registrations_view"
REG_KEY = [
    "mistudio_base_url",
    "mistudio_dataset_id",
    "role",
    "mapping_sha256",
    "columns_sha256",
    sa.literal_column("coalesce(manifest_sha256, '')"),
]
REG_WHERE = sa.text("kind = 'view'")
CACHE = "dw_text_separability"


def _sources_guard(permitted: str) -> str:
    """0005's ``dw_sources_guard`` with the given permitted list (only the list differs)."""
    return f"""
CREATE OR REPLACE FUNCTION dw_sources_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  permitted text[] := ARRAY[{permitted}];
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'dw_sources rows are never deleted; tombstone them (state deleted)';
  END IF;
  IF OLD.state = 'importing' AND NEW.state IN ('importing', 'ready', 'failed', 'cancelled') THEN
    RETURN NEW;
  END IF;
  IF OLD.state = 'ready' AND NEW.state IN ('ready', 'deleted')
     AND (to_jsonb(NEW) - permitted) = (to_jsonb(OLD) - permitted) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'dw_sources % -> %: a ready source is frozen; only the tombstone may change it',
    OLD.state, NEW.state;
END;
$$;
"""


PERMITTED_0005 = "'state', 'ready_at', 'error', 'deleted_by', 'deleted_by_origin', 'deleted_at'"
#: D56: the operator's session-data mark is the one change a ready source accepts besides the
#: tombstone (the service refuses to clear it while anything derives from the source).
PERMITTED_0035 = PERMITTED_0005 + ", 'session_data', 'session_data_basis'"


def upgrade() -> None:
    for table in ("dw_detector_sets", "dw_rubrics"):
        op.add_column(table, sa.Column("behaviour_key", sa.Text()))
        op.create_check_constraint(op.f(f"ck_{table}_behaviour_key_slug"), table, SLUG_CHECK)
        op.create_index(op.f(f"ix_{table}_behaviour_key"), table, ["behaviour_key"])

    op.add_column(REG, sa.Column("meaning_sha256", sa.String(length=64)))
    op.drop_index(REG_INDEX, table_name=REG, postgresql_where=REG_WHERE)
    op.create_index(
        REG_INDEX,
        REG,
        [*REG_KEY, sa.literal_column("coalesce(meaning_sha256, '')")],
        unique=True,
        postgresql_where=REG_WHERE,
    )

    op.add_column(
        "dw_detector_send_steps", sa.Column("behaviour", postgresql.JSONB(astext_type=sa.Text()))
    )

    op.add_column(
        "dw_sources",
        sa.Column("session_data", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("dw_sources", sa.Column("session_data_basis", sa.String(length=16)))
    op.create_check_constraint(
        op.f("ck_dw_sources_session_data_basis_valid"),
        "dw_sources",
        "session_data_basis IS NULL OR session_data_basis IN ('recognised', 'operator')",
    )
    op.create_check_constraint(
        op.f("ck_dw_sources_session_data_has_basis"),
        "dw_sources",
        "session_data = (session_data_basis IS NOT NULL)",
    )
    op.execute(_sources_guard(PERMITTED_0035))

    op.create_table(
        CACHE,
        sa.Column(
            "version_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey(
                "dw_versions.id",
                name=op.f(f"fk_{CACHE}_version_id_dw_versions"),
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("input_column", sa.Text(), nullable=False),
        sa.Column("label_column", sa.Text(), nullable=False),
        sa.Column("mapping_sha256", sa.String(length=64), nullable=False),
        sa.Column("pair_column", sa.Text(), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("measure_version", sa.String(length=32), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("n_positive", sa.Integer(), nullable=False),
        sa.Column("n_negative", sa.Integer(), nullable=False),
        sa.Column("auroc", sa.Float()),
        sa.Column("folds", sa.Integer(), nullable=False),
        sa.Column("grouped", sa.Boolean(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint(
            "version_id",
            "split",
            "input_column",
            "label_column",
            "mapping_sha256",
            "pair_column",
            "sample_n",
            "measure_version",
            name=op.f(f"pk_{CACHE}"),
        ),
        sa.CheckConstraint("n >= 0", name=op.f(f"ck_{CACHE}_n_nonnegative")),
        sa.CheckConstraint(
            "auroc IS NULL OR (auroc >= 0 AND auroc <= 1)", name=op.f(f"ck_{CACHE}_auroc_range")
        ),
    )


def downgrade() -> None:
    op.drop_table(CACHE)
    op.execute(_sources_guard(PERMITTED_0005))
    op.drop_constraint(op.f("ck_dw_sources_session_data_has_basis"), "dw_sources", type_="check")
    op.drop_constraint(op.f("ck_dw_sources_session_data_basis_valid"), "dw_sources", type_="check")
    op.drop_column("dw_sources", "session_data_basis")
    op.drop_column("dw_sources", "session_data")
    op.drop_column("dw_detector_send_steps", "behaviour")
    op.drop_index(REG_INDEX, table_name=REG, postgresql_where=REG_WHERE)
    op.create_index(REG_INDEX, REG, REG_KEY, unique=True, postgresql_where=REG_WHERE)
    op.drop_column(REG, "meaning_sha256")
    for table in ("dw_rubrics", "dw_detector_sets"):
        op.drop_index(op.f(f"ix_{table}_behaviour_key"), table_name=table)
        op.drop_constraint(op.f(f"ck_{table}_behaviour_key_slug"), table, type_="check")
        op.drop_column(table, "behaviour_key")
