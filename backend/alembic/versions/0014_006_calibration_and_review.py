"""Feature 006: calibration sets, records, checks, verdicts, targets; review queues, items,
decisions and audits (FTDD 006 section 4.2).

Ten tables plus append-only triggers on ``dw_review_decisions`` and
``dw_calibration_records``: a decision or a record is never updated or deleted (FR-006.23,
FR-006.6). The trigger function ``dw_append_only()`` is migration 0005's (shared with 001 and 002).
Version deletion tombstones ``dw_versions`` rows (P-15), so the trigger never blocks 002.
Downgrade drops the triggers and the tables in reverse dependency order.

Revision ID: 0014
Revises: 0013 (feature 010)
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = ("dw_review_decisions", "dw_calibration_records")


def upgrade() -> None:
    op.create_table(
        "dw_calibration_targets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("set_by", sa.Text(), nullable=False),
        sa.Column("set_by_origin", sa.String(length=16), nullable=False),
        sa.Column("approval_id", sa.String(length=40), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(set_by_origin = 'agent') = (approval_id IS NOT NULL AND approved_by IS NOT NULL)",
            name=op.f("ck_dw_calibration_targets_agent_iff_approved"),
        ),
        sa.CheckConstraint(
            "question_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_calibration_targets_question_hash_pattern"),
        ),
        sa.CheckConstraint(
            "set_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_calibration_targets_origin_valid"),
        ),
        sa.CheckConstraint(
            "length(set_by) > 0", name=op.f("ck_dw_calibration_targets_set_by_present")
        ),
        sa.CheckConstraint(
            "target > 0.5 AND target < 1", name=op.f("ck_dw_calibration_targets_target_range")
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["dw_approvals.id"],
            name=op.f("fk_dw_calibration_targets_approval_id_dw_approvals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_calibration_targets")),
    )
    op.create_index(
        "ix_dw_calibration_targets_question_created",
        "dw_calibration_targets",
        ["question_hash", "created_at"],
        unique=False,
    )
    op.create_table(
        "dw_review_queues",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("label_run_id", sa.String(length=40), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("label_set", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("show_model_output", sa.Boolean(), nullable=False),
        sa.Column("sample_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("origin_app", sa.Text(), nullable=True),
        sa.Column("external_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'external') = (version_id IS NULL)",
            name=op.f("ck_dw_review_queues_version_unless_external"),
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_review_queues_origin_valid"),
        ),
        sa.CheckConstraint(
            "kind <> 'external' OR origin_app IS NOT NULL",
            name=op.f("ck_dw_review_queues_external_has_app"),
        ),
        sa.CheckConstraint(
            "kind <> 'label_review' OR label_run_id IS NOT NULL",
            name=op.f("ck_dw_review_queues_review_has_run"),
        ),
        sa.CheckConstraint(
            "kind IN ('label_review', 'calibration_labeling', 'audit', 'external')",
            name=op.f("ck_dw_review_queues_kind_valid"),
        ),
        sa.CheckConstraint(
            "question_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_review_queues_question_hash_pattern"),
        ),
        sa.CheckConstraint(
            "state IN ('open', 'closed')", name=op.f("ck_dw_review_queues_state_valid")
        ),
        sa.CheckConstraint(
            "length(created_by) > 0", name=op.f("ck_dw_review_queues_created_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["label_run_id"],
            ["dw_label_runs.id"],
            name=op.f("fk_dw_review_queues_label_run_id_dw_label_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_review_queues_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_review_queues")),
    )
    op.create_index(
        "ix_dw_review_queues_created_at", "dw_review_queues", ["created_at"], unique=False
    )
    op.create_table(
        "dw_audits",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("queue_id", sa.String(length=40), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("strata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')", name=op.f("ck_dw_audits_origin_valid")
        ),
        sa.CheckConstraint(
            "state IN ('in_progress', 'complete', 'superseded')",
            name=op.f("ck_dw_audits_state_valid"),
        ),
        sa.CheckConstraint("length(created_by) > 0", name=op.f("ck_dw_audits_created_by_present")),
        sa.CheckConstraint("size BETWEEN 50 AND 100", name=op.f("ck_dw_audits_size_in_range")),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            ["dw_review_queues.id"],
            name=op.f("fk_dw_audits_queue_id_dw_review_queues"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_audits_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_audits")),
    )
    op.create_index(
        "ix_dw_audits_version_created", "dw_audits", ["version_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_dw_audits_one_in_progress",
        "dw_audits",
        ["version_id"],
        unique=True,
        postgresql_where=sa.text("state = 'in_progress'"),
    )
    op.create_table(
        "dw_calibration_sets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("label_set", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_queue_id", sa.String(length=40), nullable=True),
        sa.Column("mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("ratings_sorted", sa.Boolean(), nullable=True),
        sa.Column("licence_class", sa.String(length=32), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_calibration_sets_origin_valid"),
        ),
        sa.CheckConstraint(
            "licence_class IN ('permits_redistribution', 'private_only', 'forbids_redistribution')",
            name=op.f("ck_dw_calibration_sets_licence_class_valid"),
        ),
        sa.CheckConstraint(
            "mapping_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_calibration_sets_mapping_hash_pattern"),
        ),
        sa.CheckConstraint(
            "question_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_calibration_sets_question_hash_pattern"),
        ),
        sa.CheckConstraint(
            "source_kind <> 'review' OR source_queue_id IS NOT NULL",
            name=op.f("ck_dw_calibration_sets_review_has_queue"),
        ),
        sa.CheckConstraint(
            "source_kind IN ('imported', 'review')",
            name=op.f("ck_dw_calibration_sets_source_kind_valid"),
        ),
        sa.CheckConstraint(
            "length(created_by) > 0", name=op.f("ck_dw_calibration_sets_created_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["source_queue_id"],
            ["dw_review_queues.id"],
            name=op.f("fk_dw_calibration_sets_source_queue_id_dw_review_queues"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_calibration_sets_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_calibration_sets")),
        sa.UniqueConstraint(
            "version_id", "question_hash", "mapping_hash", name="uq_dw_calibration_sets_identity"
        ),
    )
    op.create_table(
        "dw_review_items",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("queue_id", sa.String(length=40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("model_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("stratum", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "row_key IS NULL OR row_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_review_items_row_key_pattern"),
        ),
        sa.CheckConstraint(
            "(row_key IS NULL) <> (external_id IS NULL)",
            name=op.f("ck_dw_review_items_row_key_xor_external"),
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            ["dw_review_queues.id"],
            name=op.f("fk_dw_review_items_queue_id_dw_review_queues"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_review_items")),
        sa.UniqueConstraint("queue_id", "external_id", name="uq_dw_review_items_queue_external_id"),
        sa.UniqueConstraint("queue_id", "position", name="uq_dw_review_items_queue_position"),
        sa.UniqueConstraint("queue_id", "row_key", name="uq_dw_review_items_queue_row_key"),
    )
    op.create_table(
        "dw_calibration_records",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("calibration_set_id", sa.String(length=40), nullable=False),
        sa.Column("label_run_id", sa.String(length=40), nullable=False),
        sa.Column("labeler_identity", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("labeler_identity_hash", sa.String(length=64), nullable=False),
        sa.Column("labeler_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("score_kind", sa.String(length=16), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metrics_sha256", sa.String(length=64), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_calibration_records_origin_valid"),
        ),
        sa.CheckConstraint(
            "metrics_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_calibration_records_metrics_sha256_pattern"),
        ),
        sa.CheckConstraint(
            "score_kind IN ('probability', 'distribution', 'discrete')",
            name=op.f("ck_dw_calibration_records_score_kind_valid"),
        ),
        sa.CheckConstraint(
            "length(created_by) > 0", name=op.f("ck_dw_calibration_records_created_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["calibration_set_id"],
            ["dw_calibration_sets.id"],
            name=op.f("fk_dw_calibration_records_calibration_set_id_dw_calibration_sets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["dw_jobs.id"],
            name=op.f("fk_dw_calibration_records_job_id_dw_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["label_run_id"],
            ["dw_label_runs.id"],
            name=op.f("fk_dw_calibration_records_label_run_id_dw_label_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_calibration_records")),
    )
    op.create_index(
        "ix_dw_calibration_records_fingerprint",
        "dw_calibration_records",
        ["labeler_fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_dw_calibration_records_identity_created",
        "dw_calibration_records",
        ["labeler_identity_hash", "created_at"],
        unique=False,
    )
    op.create_table(
        "dw_review_decisions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("item_id", sa.String(length=40), nullable=False),
        sa.Column("queue_id", sa.String(length=40), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=True),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("override_label", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model_output_visible", sa.Boolean(), nullable=False),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column("decided_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'override') = (override_label IS NOT NULL)",
            name=op.f("ck_dw_review_decisions_override_label_iff"),
        ),
        sa.CheckConstraint(
            "decided_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_review_decisions_origin_valid"),
        ),
        sa.CheckConstraint(
            "decision IN ('accept', 'override', 'flag', 'reject')",
            name=op.f("ck_dw_review_decisions_decision_valid"),
        ),
        sa.CheckConstraint(
            "question_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_review_decisions_question_hash_pattern"),
        ),
        sa.CheckConstraint(
            "length(decided_by) > 0", name=op.f("ck_dw_review_decisions_decided_by_present")
        ),
        sa.CheckConstraint(
            "length(reason) > 0", name=op.f("ck_dw_review_decisions_reason_present")
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["dw_review_items.id"],
            name=op.f("fk_dw_review_decisions_item_id_dw_review_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["queue_id"],
            ["dw_review_queues.id"],
            name=op.f("fk_dw_review_decisions_queue_id_dw_review_queues"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_review_decisions_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_review_decisions")),
    )
    op.create_index(
        "ix_dw_review_decisions_item_created",
        "dw_review_decisions",
        ["item_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_dw_review_decisions_queue_created",
        "dw_review_decisions",
        ["queue_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_dw_review_decisions_row_key_question",
        "dw_review_decisions",
        ["row_key", "question_hash", "created_at"],
        unique=False,
    )
    op.create_table(
        "dw_calibration_checks",
        sa.Column("record_id", sa.String(length=40), nullable=False),
        sa.Column("check_id", sa.String(length=64), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("check_version", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("statistic", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "result IN ('pass', 'fail', 'not_applicable')",
            name=op.f("ck_dw_calibration_checks_result_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["dw_calibration_records.id"],
            name=op.f("fk_dw_calibration_checks_record_id_dw_calibration_records"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "record_id", "check_id", "metric_id", name=op.f("pk_dw_calibration_checks")
        ),
    )
    op.create_table(
        "dw_calibration_set_labels",
        sa.Column("calibration_set_id", sa.String(length=40), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("human_label", sa.Text(), nullable=True),
        sa.Column("group_key", sa.Text(), nullable=True),
        sa.Column("strata_key", sa.Text(), nullable=True),
        sa.Column("is_reference", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ratings", postgresql.ARRAY(sa.SmallInteger()), nullable=True),
        sa.Column("decision_id", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "row_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_dw_calibration_set_labels_row_key_pattern")
        ),
        sa.ForeignKeyConstraint(
            ["calibration_set_id"],
            ["dw_calibration_sets.id"],
            name=op.f("fk_dw_calibration_set_labels_calibration_set_id_dw_calibration_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["dw_review_decisions.id"],
            name=op.f("fk_dw_calibration_set_labels_decision_id_dw_review_decisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "calibration_set_id", "row_key", name=op.f("pk_dw_calibration_set_labels")
        ),
        sa.UniqueConstraint(
            "calibration_set_id", "position", name="uq_dw_calibration_set_labels_position"
        ),
    )
    op.create_table(
        "dw_calibration_verdicts",
        sa.Column("record_id", sa.String(length=40), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("rule", sa.String(length=16), nullable=True),
        sa.Column("numbers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_id", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "rule IS NULL OR rule IN ('operator_target', 'held_out_rater', 'default_c3')",
            name=op.f("ck_dw_calibration_verdicts_rule_valid"),
        ),
        sa.CheckConstraint(
            "verdict IN ('passes', 'fails', 'invalid', 'insufficient')",
            name=op.f("ck_dw_calibration_verdicts_verdict_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["dw_calibration_records.id"],
            name=op.f("fk_dw_calibration_verdicts_record_id_dw_calibration_records"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["dw_calibration_targets.id"],
            name=op.f("fk_dw_calibration_verdicts_target_id_dw_calibration_targets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("record_id", name=op.f("pk_dw_calibration_verdicts")),
    )

    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
        )


def downgrade() -> None:
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.drop_table("dw_calibration_verdicts")
    op.drop_table("dw_calibration_set_labels")
    op.drop_table("dw_calibration_checks")
    op.drop_index("ix_dw_review_decisions_row_key_question", table_name="dw_review_decisions")
    op.drop_index("ix_dw_review_decisions_queue_created", table_name="dw_review_decisions")
    op.drop_index("ix_dw_review_decisions_item_created", table_name="dw_review_decisions")
    op.drop_table("dw_review_decisions")
    op.drop_index("ix_dw_calibration_records_identity_created", table_name="dw_calibration_records")
    op.drop_index("ix_dw_calibration_records_fingerprint", table_name="dw_calibration_records")
    op.drop_table("dw_calibration_records")
    op.drop_table("dw_review_items")
    op.drop_table("dw_calibration_sets")
    op.drop_index(
        "uq_dw_audits_one_in_progress",
        table_name="dw_audits",
        postgresql_where=sa.text("state = 'in_progress'"),
    )
    op.drop_index("ix_dw_audits_version_created", table_name="dw_audits")
    op.drop_table("dw_audits")
    op.drop_index("ix_dw_review_queues_created_at", table_name="dw_review_queues")
    op.drop_table("dw_review_queues")
    op.drop_index("ix_dw_calibration_targets_question_created", table_name="dw_calibration_targets")
    op.drop_table("dw_calibration_targets")
