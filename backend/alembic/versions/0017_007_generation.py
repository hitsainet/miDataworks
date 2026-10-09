"""Feature 007: generation templates, runs, steering snapshots, chunks, records, pairs, diversity.

FTDD 007 section 4.1, FTID 007 section 4. Snapshots, records, pairs and diversity reports are
append-only (the shared ``dw_append_only`` trigger of migration 0005); a template refuses an
UPDATE once it has been used (``dw_generation_templates_immutable``).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

APPEND_ONLY = (
    "dw_steering_snapshots",
    "dw_generation_records",
    "dw_generation_pairs",
    "dw_diversity_reports",
)

TEMPLATE_FUNCTION = """
CREATE FUNCTION dw_generation_templates_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.used_at IS NOT NULL THEN
      RAISE EXCEPTION 'TEMPLATE_IMMUTABLE: a used generation template cannot be deleted';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.used_at IS NOT NULL THEN
    RAISE EXCEPTION 'TEMPLATE_IMMUTABLE: a used generation template cannot be changed';
  END IF;
  IF NEW.body IS DISTINCT FROM OLD.body OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
     OR NEW.name IS DISTINCT FROM OLD.name OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.kind IS DISTINCT FROM OLD.kind THEN
    RAISE EXCEPTION 'TEMPLATE_IMMUTABLE: a template body is fixed; clone it to change it';
  END IF;
  RETURN NEW;
END $$;
"""


def _origin(column: str = "started_by_origin", extra: str = "") -> sa.CheckConstraint:
    values = "'operator', 'agent'" + extra
    return sa.CheckConstraint(f"{column} IN ({values})", name="origin_valid")


def upgrade() -> None:
    op.create_table(
        "dw_generation_templates",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("body", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("builtin", sa.Boolean(), nullable=False),
        sa.Column("cloned_from", sa.String(length=40), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("kind IN ('expand', 'respond')", name="kind_valid"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        _origin("created_by_origin", ", 'system'"),
        sa.CheckConstraint("length(created_by) > 0", name="created_by_present"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_pattern"),
        sa.ForeignKeyConstraint(
            ["cloned_from"], ["dw_generation_templates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_dw_generation_templates_name_version"),
    )
    op.create_table(
        "dw_generation_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("input_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("held_out_origin_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("held_out_splits", JSONB, nullable=False),
        sa.Column("prompt_column", sa.Text(), nullable=False),
        sa.Column("seed_splits", JSONB, nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("n_responses", sa.SmallInteger(), nullable=False),
        sa.Column("expand_template_id", sa.String(length=40), nullable=True),
        sa.Column("respond_template_id", sa.String(length=40), nullable=True),
        sa.Column("stages", JSONB, nullable=False),
        sa.Column("generation_endpoint", JSONB, nullable=False),
        sa.Column("server_kind", sa.String(length=24), nullable=False),
        sa.Column("generator_identities", JSONB, nullable=False),
        sa.Column("judge_identity", JSONB, nullable=True),
        sa.Column("judge_identity_hash", sa.String(length=64), nullable=True),
        sa.Column("independence_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chosen_side", sa.String(length=1), nullable=True),
        sa.Column("engine_path", sa.String(length=8), nullable=False),
        sa.Column("steering_supported", sa.Boolean(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=True),
        sa.Column("revision_reported", sa.Boolean(), nullable=True),
        sa.Column("model_revision", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("failure_reason", sa.String(length=32), nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("counts", JSONB, nullable=False),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("started_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('standard', 'steered_pairs')", name="mode_valid"),
        sa.CheckConstraint(
            "state IN ('planned', 'queued', 'running', 'completed', 'cancelled', 'failed')",
            name="state_valid",
        ),
        sa.CheckConstraint(
            "target_type IN ('sft', 'kto', 'grpo_prompt', 'dpo')", name="target_valid"
        ),
        sa.CheckConstraint("engine_path IN ('relay', 'native')", name="engine_path_valid"),
        sa.CheckConstraint("n_responses BETWEEN 1 AND 16", name="n_responses_range"),
        sa.CheckConstraint("sample_size BETWEEN 1 AND 100000", name="sample_size_range"),
        sa.CheckConstraint(
            "mode <> 'steered_pairs' OR (n_responses = 1 AND chosen_side IN ('a', 'b'))",
            name="steered_shape",
        ),
        sa.CheckConstraint(
            "mode <> 'standard' OR chosen_side IS NULL", name="standard_has_no_chosen_side"
        ),
        _origin(),
        sa.CheckConstraint("length(started_by) > 0", name="started_by_present"),
        sa.ForeignKeyConstraint(["input_version_id"], ["dw_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["held_out_origin_version_id"], ["dw_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expand_template_id"], ["dw_generation_templates.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["respond_template_id"], ["dw_generation_templates.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dw_generation_runs_version_created",
        "dw_generation_runs",
        ["input_version_id", "created_at"],
    )
    op.create_table(
        "dw_generation_run_jobs",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["dw_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "seq"),
    )
    op.create_index(
        "ix_dw_generation_run_jobs_job", "dw_generation_run_jobs", ["job_id"], unique=True
    )
    op.create_table(
        "dw_steering_snapshots",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("profile_id", sa.Text(), nullable=True),
        sa.Column("profile_name", sa.Text(), nullable=True),
        sa.Column("profile_updated_at", sa.Text(), nullable=True),
        sa.Column("intensity", sa.Float(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("sae_id", sa.Text(), nullable=True),
        sa.Column("layer", sa.Integer(), nullable=True),
        sa.Column("features", JSONB, nullable=False),
        sa.Column("sent_features", JSONB, nullable=False),
        sa.Column("set_hash", sa.String(length=71), nullable=True),
        sa.Column("body_overrides", JSONB, nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("side IN ('generator', 'a', 'b')", name="side_valid"),
        sa.CheckConstraint("kind IN ('none', 'profile', 'inline')", name="kind_valid"),
        sa.CheckConstraint(
            "(kind = 'none' AND set_hash IS NULL) OR "
            "(kind <> 'none' AND set_hash ~ '^sha256:[0-9a-f]{64}$')",
            name="set_hash_shape",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "side"),
    )
    op.create_table(
        "dw_generation_chunks",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=8), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("stage IN ('expand', 'respond')", name="stage_valid"),
        sa.ForeignKeyConstraint(["run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["dw_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "stage", "chunk_index"),
    )
    op.create_table(
        "dw_generation_records",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=8), nullable=False),
        sa.Column("record_index", sa.BigInteger(), nullable=False),
        sa.Column("seed_position", sa.Integer(), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=True),
        sa.Column("seed_row_key", sa.String(length=64), nullable=False),
        sa.Column("prompt_row_key", sa.String(length=64), nullable=False),
        sa.Column("response_index", sa.Integer(), nullable=False),
        sa.Column("side", sa.String(length=1), nullable=True),
        sa.Column("template_id", sa.String(length=40), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("model_revision", sa.Text(), nullable=True),
        sa.Column("requested_set_hash", sa.String(length=71), nullable=True),
        sa.Column("reported_steering", sa.Text(), nullable=True),
        sa.Column("steering_check", sa.String(length=16), nullable=False),
        sa.Column("check_reasons", JSONB, nullable=False),
        sa.Column("seed_sent", sa.BigInteger(), nullable=True),
        sa.Column("seed_confirmed", sa.Boolean(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.CheckConstraint("stage IN ('expand', 'respond')", name="stage_valid"),
        sa.CheckConstraint(
            "steering_check IN ('match', 'mismatch', 'unreported', 'not_applicable')",
            name="check_valid",
        ),
        sa.CheckConstraint(
            "outcome IN ('generated', 'discarded', 'skipped')", name="outcome_valid"
        ),
        sa.CheckConstraint("side IS NULL OR side IN ('a', 'b')", name="side_valid"),
        sa.CheckConstraint(
            "(outcome = 'generated' AND reason_code IS NULL) OR "
            "(outcome <> 'generated' AND reason_code IS NOT NULL)",
            name="reason_when_not_generated",
        ),
        sa.CheckConstraint(
            "outcome <> 'generated' OR steering_check IN ('match', 'not_applicable') OR "
            "(steering_check = 'unreported' AND requested_set_hash IS NULL)",
            name="generated_only_when_checked",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "stage", "record_index"),
    )
    op.create_index(
        "ix_dw_generation_records_run_outcome", "dw_generation_records", ["run_id", "outcome"]
    )
    op.create_index("ix_dw_generation_records_row_key", "dw_generation_records", ["row_key"])
    op.create_table(
        "dw_generation_pairs",
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("prompt_row_key", sa.String(length=64), nullable=False),
        sa.Column("pair_index", sa.Integer(), nullable=False),
        sa.Column("record_index_a", sa.BigInteger(), nullable=False),
        sa.Column("record_index_b", sa.BigInteger(), nullable=False),
        sa.Column("chosen_side", sa.String(length=1), nullable=False),
        sa.Column("shared_seed", sa.BigInteger(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", "prompt_row_key", "pair_index"),
    )
    op.create_table(
        "dw_diversity_reports",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("reference_version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("column", sa.Text(), nullable=False),
        sa.Column("method_hash", sa.String(length=64), nullable=False),
        sa.Column("method", JSONB, nullable=False),
        sa.Column("splits", JSONB, nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("embedding_identity", JSONB, nullable=True),
        sa.Column("clustering", JSONB, nullable=False),
        sa.Column("figures", JSONB, nullable=False),
        sa.Column("checks", JSONB, nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(length=40), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "verdict IN ('holds', 'falls', 'not_measured', 'invalid')", name="verdict_valid"
        ),
        _origin("created_by_origin", ", 'system'"),
        sa.ForeignKeyConstraint(["version_id"], ["dw_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reference_version_id"], ["dw_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["dw_jobs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "version_id", "column", "method_hash", name="uq_dw_diversity_reports_identity"
        ),
    )
    op.create_index(
        "ix_dw_diversity_reports_version", "dw_diversity_reports", ["version_id", "created_at"]
    )
    op.execute(TEMPLATE_FUNCTION)
    op.execute(
        "CREATE TRIGGER dw_generation_templates_immutable BEFORE UPDATE OR DELETE ON "
        "dw_generation_templates FOR EACH ROW EXECUTE FUNCTION dw_generation_templates_immutable()"
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute(
        "DROP TRIGGER IF EXISTS dw_generation_templates_immutable ON dw_generation_templates"
    )
    op.execute("DROP FUNCTION IF EXISTS dw_generation_templates_immutable()")
    op.drop_index("ix_dw_diversity_reports_version", table_name="dw_diversity_reports")
    op.drop_table("dw_diversity_reports")
    op.drop_table("dw_generation_pairs")
    op.drop_index("ix_dw_generation_records_row_key", table_name="dw_generation_records")
    op.drop_index("ix_dw_generation_records_run_outcome", table_name="dw_generation_records")
    op.drop_table("dw_generation_records")
    op.drop_table("dw_generation_chunks")
    op.drop_table("dw_steering_snapshots")
    op.drop_index("ix_dw_generation_run_jobs_job", table_name="dw_generation_run_jobs")
    op.drop_table("dw_generation_run_jobs")
    op.drop_index("ix_dw_generation_runs_version_created", table_name="dw_generation_runs")
    op.drop_table("dw_generation_runs")
    op.drop_table("dw_generation_templates")
