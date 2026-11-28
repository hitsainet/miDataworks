"""dw_version_reports and dw_shortcut_levels: feature 004's reports and warning levels (FTDD 004 §4).

A completed report is immutable (trigger ``dw_version_reports_immutable``); levels are append-only
history whose ``origin`` must be ``'operator'`` (P-09).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE_FUNCTION = """
CREATE FUNCTION dw_version_reports_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.state = 'completed' THEN
      RAISE EXCEPTION 'a completed dw_version_reports row is immutable (FTDD 004 section 4.1)';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.state = 'completed' THEN
    RAISE EXCEPTION 'a completed dw_version_reports row is immutable (FTDD 004 section 4.1)';
  END IF;
  RETURN NEW;
END $$;
"""


def upgrade() -> None:
    op.create_table(
        "dw_version_reports",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("operator_name", sa.String(length=64), nullable=False),
        sa.Column("operator_version", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs_digest", sa.String(length=64), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "artefacts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("job_id", sa.String(length=40), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("started_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('profile', 'shortcut_audit', 'leakage', 'contamination', 'clusters', "
            "'trl_validation')",
            name=op.f("ck_dw_version_reports_kind_valid"),
        ),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_dw_version_reports_state_valid"),
        ),
        sa.CheckConstraint(
            "started_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_version_reports_origin_valid"),
        ),
        sa.CheckConstraint(
            "params_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_dw_version_reports_params_hash_pattern")
        ),
        sa.CheckConstraint(
            "inputs_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_version_reports_inputs_digest_pattern"),
        ),
        sa.CheckConstraint(
            "length(btrim(started_by)) > 0", name=op.f("ck_dw_version_reports_started_by_present")
        ),
        sa.CheckConstraint(
            "(state = 'completed') = (result IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_dw_version_reports_completed_has_result"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_version_reports_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["dw_jobs.id"],
            name=op.f("fk_dw_version_reports_job_id_dw_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_version_reports")),
    )
    op.create_index(
        "uq_dw_version_reports_completed",
        "dw_version_reports",
        [
            "version_id",
            "kind",
            "operator_name",
            "operator_version",
            "params_hash",
            "inputs_digest",
        ],
        unique=True,
        postgresql_where=sa.text("state = 'completed'"),
    )
    op.create_index(
        "ix_dw_version_reports_version_kind", "dw_version_reports", ["version_id", "kind"]
    )
    op.execute(IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER dw_version_reports_immutable BEFORE UPDATE OR DELETE ON "
        "dw_version_reports FOR EACH ROW EXECUTE FUNCTION dw_version_reports_immutable()"
    )

    op.create_table(
        "dw_shortcut_levels",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("dataset_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("margin_pp", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("set_by", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "scope IN ('global', 'dataset')", name=op.f("ck_dw_shortcut_levels_scope_valid")
        ),
        sa.CheckConstraint(
            "action IN ('set', 'clear')", name=op.f("ck_dw_shortcut_levels_action_valid")
        ),
        sa.CheckConstraint(
            "(scope = 'global') = (dataset_id IS NULL)",
            name=op.f("ck_dw_shortcut_levels_scope_dataset_coherent"),
        ),
        sa.CheckConstraint(
            "action = 'set' OR scope = 'dataset'",
            name=op.f("ck_dw_shortcut_levels_clear_only_for_dataset"),
        ),
        sa.CheckConstraint(
            "(action = 'clear' AND margin_pp IS NULL) OR "
            "(action = 'set' AND margin_pp IS NOT NULL AND margin_pp >= 0 AND margin_pp < 100)",
            name=op.f("ck_dw_shortcut_levels_margin_range"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_dw_shortcut_levels_reason_present")
        ),
        sa.CheckConstraint(
            "length(btrim(set_by)) > 0", name=op.f("ck_dw_shortcut_levels_set_by_present")
        ),
        sa.CheckConstraint(
            "origin = 'operator'", name=op.f("ck_dw_shortcut_levels_origin_operator_only")
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["dw_datasets.id"],
            name=op.f("fk_dw_shortcut_levels_dataset_id_dw_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_shortcut_levels")),
    )
    op.create_index(
        "ix_dw_shortcut_levels_scope",
        "dw_shortcut_levels",
        ["scope", "dataset_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_dw_shortcut_levels_scope", table_name="dw_shortcut_levels")
    op.drop_table("dw_shortcut_levels")
    op.execute("DROP TRIGGER dw_version_reports_immutable ON dw_version_reports")
    op.execute("DROP FUNCTION dw_version_reports_immutable()")
    op.drop_index("ix_dw_version_reports_version_kind", table_name="dw_version_reports")
    op.drop_index("uq_dw_version_reports_completed", table_name="dw_version_reports")
    op.drop_table("dw_version_reports")
