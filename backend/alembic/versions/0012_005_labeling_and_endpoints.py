"""Feature 005: decision templates, rubrics, label runs, labels, the shared miLLM lease.

Eight tables (FTDD 005 section 4.1 counts seven; the lease members are the eighth, given their
own table): dw_decision_templates, dw_rubrics, dw_label_runs, dw_label_run_jobs,
dw_label_run_chunks, dw_labels, dw_model_leases, dw_model_lease_members. Downgrade drops them.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dw_decision_templates",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("protocol", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=True),
        sa.Column("bound_model_id", sa.String(length=255), nullable=True),
        sa.Column("bound_model_revision", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT (body ? 'verbalizer_ids') OR bound_model_id IS NOT NULL",
            name=op.f("ck_dw_decision_templates_token_ids_bound_to_model"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_decision_templates_content_hash_pattern"),
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')",
            name=op.f("ck_dw_decision_templates_origin_valid"),
        ),
        sa.CheckConstraint(
            "protocol IN ('openai_scoring', 'tei_classification', 'plugin')",
            name=op.f("ck_dw_decision_templates_protocol_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_dw_decision_templates_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_decision_templates")),
        sa.UniqueConstraint("content_hash", name="uq_dw_decision_templates_content_hash"),
        sa.UniqueConstraint("name", "version", name="uq_dw_decision_templates_name_version"),
    )
    op.create_table(
        "dw_model_leases",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("millm_model_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("lease_id_ciphertext", sa.Text(), nullable=False),
        sa.Column("holder", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lost_reason", sa.Text(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('active', 'released', 'lost')", name=op.f("ck_dw_model_leases_state_valid")
        ),
        sa.CheckConstraint(
            "ttl_seconds BETWEEN 1 AND 7200", name=op.f("ck_dw_model_leases_ttl_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_model_leases")),
    )
    op.create_index(
        "uq_dw_model_leases_active_base_url",
        "dw_model_leases",
        ["base_url"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_table(
        "dw_rubrics",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("style", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_dw_rubrics_content_hash_pattern")
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent', 'system')",
            name=op.f("ck_dw_rubrics_origin_valid"),
        ),
        sa.CheckConstraint(
            "style IN ('pointwise', 'pairwise', 'binary', 'stepwise')",
            name=op.f("ck_dw_rubrics_style_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_dw_rubrics_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_rubrics")),
        sa.UniqueConstraint("content_hash", name="uq_dw_rubrics_content_hash"),
        sa.UniqueConstraint("name", "version", name="uq_dw_rubrics_name_version"),
    )
    op.create_table(
        "dw_model_lease_members",
        sa.Column("lease_row_id", sa.String(length=40), nullable=False),
        sa.Column("member_id", sa.String(length=80), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["lease_row_id"],
            ["dw_model_leases.id"],
            name=op.f("fk_dw_model_lease_members_lease_row_id_dw_model_leases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "lease_row_id", "member_id", name=op.f("pk_dw_model_lease_members")
        ),
    )
    op.create_table(
        "dw_label_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("input_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("field_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("endpoint_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("template_id", sa.String(length=40), nullable=True),
        sa.Column("rubric_id", sa.String(length=40), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("positive_label", sa.Text(), nullable=True),
        sa.Column("negative_label", sa.Text(), nullable=True),
        sa.Column("threshold_positive", sa.Float(), nullable=True),
        sa.Column("threshold_negative", sa.Float(), nullable=True),
        sa.Column("min_top_probability", sa.Float(), nullable=True),
        sa.Column("label_set", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sampling", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("structured_output", sa.String(length=16), nullable=False),
        sa.Column("packing", sa.String(length=16), nullable=False),
        sa.Column("batch_id", sa.String(length=128), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("row_filter", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("parent_run_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("labeler_identity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("labeler_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("labeler_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=True),
        sa.Column("revision_reported", sa.Boolean(), nullable=True),
        sa.Column("system_fingerprint", sa.Text(), nullable=True),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("keep_share_estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("keep_share_actual", sa.Float(), nullable=True),
        sa.Column("length_correlation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rows_total", sa.BigInteger(), nullable=False),
        sa.Column("rows_reused", sa.BigInteger(), nullable=False),
        sa.Column("agent_counted_rows", sa.BigInteger(), nullable=False),
        sa.Column("approval_id", sa.String(length=40), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("started_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('classifier', 'judge', 'rederived', 'aggregate')",
            name=op.f("ck_dw_label_runs_kind_valid"),
        ),
        sa.CheckConstraint(
            "packing IN ('single', 'packed', 'batch')", name=op.f("ck_dw_label_runs_packing_valid")
        ),
        sa.CheckConstraint(
            "started_by_origin IN ('operator', 'agent')", name=op.f("ck_dw_label_runs_origin_valid")
        ),
        sa.CheckConstraint(
            "state IN ('awaiting_approval', 'queued', 'running', 'cancelled', 'completed', 'failed', 'rejected')",
            name=op.f("ck_dw_label_runs_state_valid"),
        ),
        sa.CheckConstraint(
            "structured_output IN ('json_schema', 'strict_parse', 'n/a')",
            name=op.f("ck_dw_label_runs_structured_valid"),
        ),
        sa.CheckConstraint("chunk_size > 0", name=op.f("ck_dw_label_runs_chunk_size_positive")),
        sa.CheckConstraint(
            "length(started_by) > 0", name=op.f("ck_dw_label_runs_started_by_present")
        ),
        sa.CheckConstraint(
            "rows_total >= 0 AND rows_reused >= 0 AND agent_counted_rows >= 0",
            name=op.f("ck_dw_label_runs_row_counts_nonnegative"),
        ),
        sa.CheckConstraint(
            "threshold_positive IS NULL OR threshold_negative IS NULL OR (threshold_negative < threshold_positive AND threshold_negative >= 0 AND threshold_positive <= 1)",
            name=op.f("ck_dw_label_runs_thresholds_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["input_version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_label_runs_input_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rubric_id"],
            ["dw_rubrics.id"],
            name=op.f("fk_dw_label_runs_rubric_id_dw_rubrics"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["dw_decision_templates.id"],
            name=op.f("fk_dw_label_runs_template_id_dw_decision_templates"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_label_runs")),
    )
    op.create_index(
        "ix_dw_label_runs_identity_hash", "dw_label_runs", ["labeler_identity_hash"], unique=False
    )
    op.create_index(
        "ix_dw_label_runs_version_origin_created",
        "dw_label_runs",
        ["input_version_id", "started_by_origin", "created_at"],
        unique=False,
    )
    op.create_table(
        "dw_label_run_chunks",
        sa.Column("label_run_id", sa.String(length=40), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "row_count >= 0", name=op.f("ck_dw_label_run_chunks_row_count_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["label_run_id"],
            ["dw_label_runs.id"],
            name=op.f("fk_dw_label_run_chunks_label_run_id_dw_label_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("label_run_id", "chunk_index", name=op.f("pk_dw_label_run_chunks")),
    )
    op.create_table(
        "dw_label_run_jobs",
        sa.Column("label_run_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["dw_jobs.id"],
            name=op.f("fk_dw_label_run_jobs_job_id_dw_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["label_run_id"],
            ["dw_label_runs.id"],
            name=op.f("fk_dw_label_run_jobs_label_run_id_dw_label_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("label_run_id", "seq", name=op.f("pk_dw_label_run_jobs")),
        sa.UniqueConstraint("job_id", name="uq_dw_label_run_jobs_job_id"),
    )
    op.create_table(
        "dw_labels",
        sa.Column("label_run_id", sa.String(length=40), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("labeler_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=128), nullable=False),
        sa.Column("parsed_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("probability", sa.Float(), nullable=True),
        sa.Column("distribution", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("steering_state", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("provisional", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reused_from_run_id", sa.String(length=40), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column(
            "scored_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("row_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_dw_labels_row_key_pattern")),
        sa.CheckConstraint(
            "length(outcome) BETWEEN 1 AND 128", name=op.f("ck_dw_labels_outcome_present")
        ),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name=op.f("ck_dw_labels_probability_range"),
        ),
        sa.ForeignKeyConstraint(
            ["label_run_id"],
            ["dw_label_runs.id"],
            name=op.f("fk_dw_labels_label_run_id_dw_label_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("label_run_id", "row_key", name=op.f("pk_dw_labels")),
    )
    op.create_index(
        "ix_dw_labels_fingerprint_row_key",
        "dw_labels",
        ["labeler_fingerprint", "row_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dw_labels_fingerprint_row_key", table_name="dw_labels")
    op.drop_table("dw_labels")
    op.drop_table("dw_label_run_jobs")
    op.drop_table("dw_label_run_chunks")
    op.drop_index("ix_dw_label_runs_version_origin_created", table_name="dw_label_runs")
    op.drop_index("ix_dw_label_runs_identity_hash", table_name="dw_label_runs")
    op.drop_table("dw_label_runs")
    op.drop_table("dw_model_lease_members")
    op.drop_table("dw_rubrics")
    op.drop_index(
        "uq_dw_model_leases_active_base_url",
        table_name="dw_model_leases",
        postgresql_where=sa.text("state = 'active'"),
    )
    op.drop_table("dw_model_leases")
    op.drop_table("dw_decision_templates")
