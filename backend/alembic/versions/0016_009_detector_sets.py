"""Feature 009: detector sets, sends, steps, the miStudio registration map, results snapshots,
reward marks, agreement reports and length profiles (FTDD 009 section 4; FTID 009 section 4).

Also, through their owners (FTDD 009 section 4.2):
- 005's ``dw_label_runs.kind`` CHECK gains ``probe_verdict`` and ``feature_tag`` (005 stores kind as
  a CHECK-constrained string, so the constraint is replaced; FTID 009 section 4 says to follow 005);
- 008's ``dw_publishes.send_id`` (created nullable by 0011) gains its FK to ``dw_detector_sends``.

Triggers:
- ``dw_detector_sends_frozen`` refuses a change to ``snapshot``, ``snapshot_sha256``, ``checks``,
  ``plan`` or ``approval_digest`` (FR-009.12, FR-009.16, FR-009.82);
- ``dw_append_only()`` (migration 0005) on results, reward marks, agreement reports and length
  profiles: insert-only (FR-009.40, TQ9).

Downgrade refuses while label runs of the two new kinds exist: dropping the values from the CHECK
would orphan them.

Revision ID: 0016
Revises: 0015 (feature 004). Feature 007 may also claim 0016; the orchestrator settles that
at merge.
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES: tuple[str, ...] = (
    "dw_detector_results",
    "dw_reward_marks",
    "dw_agreement_reports",
    "dw_length_profiles",
)
OLD_KINDS = "kind IN ('classifier', 'judge', 'rederived', 'aggregate')"
NEW_KINDS = (
    "kind IN ('classifier', 'judge', 'rederived', 'aggregate', 'probe_verdict', 'feature_tag')"
)

FROZEN = """
CREATE FUNCTION dw_detector_sends_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.snapshot IS DISTINCT FROM OLD.snapshot
     OR NEW.snapshot_sha256 IS DISTINCT FROM OLD.snapshot_sha256
     OR NEW.checks IS DISTINCT FROM OLD.checks
     OR NEW.plan IS DISTINCT FROM OLD.plan
     OR NEW.approval_digest IS DISTINCT FROM OLD.approval_digest THEN
    RAISE EXCEPTION 'dw_detector_sends: snapshot, checks, plan and approval digest are written once';
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "dw_detector_sets",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("positive_meaning", sa.Text(), nullable=False),
        sa.Column("monitored_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_detector_sets_origin_valid"),
        ),
        sa.CheckConstraint(
            "name ~ '^[a-z0-9][a-z0-9-]{0,99}$'", name=op.f("ck_dw_detector_sets_name_pattern")
        ),
        sa.CheckConstraint(
            "length(created_by) > 0", name=op.f("ck_dw_detector_sets_created_by_present")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_detector_sets")),
        sa.UniqueConstraint("name", name=op.f("uq_dw_detector_sets_name")),
    )
    op.create_table(
        "dw_detector_sends",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("set_id", sa.String(length=40), nullable=False),
        sa.Column("job_id", sa.String(length=40), nullable=False),
        sa.Column("job_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("mistudio_base_url", sa.Text(), nullable=False),
        sa.Column("approval_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("approval_id", sa.String(length=40), nullable=True),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("started_by_origin", sa.String(length=16), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "approval_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_detector_sends_approval_digest_pattern"),
        ),
        sa.CheckConstraint(
            "snapshot_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_dw_detector_sends_snapshot_sha256_pattern"),
        ),
        sa.CheckConstraint(
            "started_by_origin <> 'agent' OR approval_id IS NOT NULL",
            name=op.f("ck_dw_detector_sends_agent_has_approval"),
        ),
        sa.CheckConstraint(
            "started_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_detector_sends_origin_valid"),
        ),
        sa.CheckConstraint(
            "state IN ('awaiting_approval', 'queued', 'running', 'completed', 'failed', 'cancelled', 'rejected')",
            name=op.f("ck_dw_detector_sends_state_valid"),
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'public')",
            name=op.f("ck_dw_detector_sends_visibility_valid"),
        ),
        sa.CheckConstraint(
            "length(started_by) > 0", name=op.f("ck_dw_detector_sends_started_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["dw_approvals.id"],
            name=op.f("fk_dw_detector_sends_approval_id_dw_approvals"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["dw_jobs.id"],
            name=op.f("fk_dw_detector_sends_job_id_dw_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["dw_detector_sets.id"],
            name=op.f("fk_dw_detector_sends_set_id_dw_detector_sets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_detector_sends")),
    )
    op.create_index(
        "ix_dw_detector_sends_set", "dw_detector_sends", ["set_id", "created_at"], unique=False
    )
    op.create_table(
        "dw_detector_results",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("set_id", sa.String(length=40), nullable=False),
        sa.Column("send_id", sa.String(length=40), nullable=False),
        sa.Column("mistudio_base_url", sa.Text(), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("read_by", sa.Text(), nullable=False),
        sa.Column("read_by_origin", sa.String(length=16), nullable=False),
        sa.Column("runs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("figures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report_sha256", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("gone", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "read_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_detector_results_origin_valid"),
        ),
        sa.CheckConstraint(
            "length(read_by) > 0", name=op.f("ck_dw_detector_results_read_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["send_id"],
            ["dw_detector_sends.id"],
            name=op.f("fk_dw_detector_results_send_id_dw_detector_sends"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["dw_detector_sets.id"],
            name=op.f("fk_dw_detector_results_set_id_dw_detector_sets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_detector_results")),
    )
    op.create_index(
        "ix_dw_detector_results_set", "dw_detector_results", ["set_id", "read_at"], unique=False
    )
    op.create_table(
        "dw_agreement_reports",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("probe_label_run_id", sa.String(length=40), nullable=False),
        sa.Column("judge_label_run_id", sa.String(length=40), nullable=False),
        sa.Column("reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("figures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("judge_is_training_labeler", sa.Boolean(), nullable=False),
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
            name=op.f("ck_dw_agreement_reports_origin_valid"),
        ),
        sa.CheckConstraint(
            "length(created_by) > 0", name=op.f("ck_dw_agreement_reports_created_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_agreement_reports_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_agreement_reports")),
    )
    op.create_table(
        "dw_detector_set_roles",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("set_id", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("input_column", sa.Text(), nullable=False),
        sa.Column("label_column", sa.Text(), nullable=False),
        sa.Column("label_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pair_column", sa.Text(), nullable=True),
        sa.Column("negatives_basis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "role <> 'calibration_negatives' OR jsonb_typeof(negatives_basis) = 'object'",
            name=op.f("ck_dw_detector_set_roles_calibration_has_basis"),
        ),
        sa.CheckConstraint(
            "role IN ('train', 'id_test', 'ood_eval', 'calibration_negatives')",
            name=op.f("ck_dw_detector_set_roles_role_valid"),
        ),
        sa.CheckConstraint(
            "length(input_column) > 0", name=op.f("ck_dw_detector_set_roles_input_column_present")
        ),
        sa.CheckConstraint(
            "length(label_column) > 0", name=op.f("ck_dw_detector_set_roles_label_column_present")
        ),
        sa.CheckConstraint(
            "length(split) > 0", name=op.f("ck_dw_detector_set_roles_split_present")
        ),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["dw_detector_sets.id"],
            name=op.f("fk_dw_detector_set_roles_set_id_dw_detector_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_detector_set_roles_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_detector_set_roles")),
    )
    op.create_index(
        "ix_dw_detector_set_roles_version", "dw_detector_set_roles", ["version_id"], unique=False
    )
    op.create_index(
        "uq_dw_detector_set_roles_single",
        "dw_detector_set_roles",
        ["set_id", "role"],
        unique=True,
        postgresql_where=sa.text("role <> 'ood_eval'"),
    )
    op.create_table(
        "dw_length_profiles",
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("column", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("measure_version", sa.String(length=32), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("quantiles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("histogram", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("permille", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "unit IN ('chars', 'words')", name=op.f("ck_dw_length_profiles_unit_valid")
        ),
        sa.CheckConstraint("n >= 0", name=op.f("ck_dw_length_profiles_n_nonnegative")),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_length_profiles_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "version_id",
            "split",
            "column",
            "unit",
            "measure_version",
            name=op.f("pk_dw_length_profiles"),
        ),
    )
    op.create_table(
        "dw_mistudio_registrations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mistudio_base_url", sa.Text(), nullable=False),
        sa.Column("repo_id", sa.Text(), nullable=True),
        sa.Column("config", sa.Text(), nullable=True),
        sa.Column("split", sa.Text(), nullable=True),
        sa.Column("commit", sa.String(length=40), nullable=True),
        sa.Column("mistudio_dataset_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("mapping_sha256", sa.String(length=64), nullable=True),
        sa.Column("columns_sha256", sa.String(length=64), nullable=True),
        sa.Column("probe_dataset_id", sa.Text(), nullable=True),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("send_id", sa.String(length=40), nullable=True),
        sa.Column("registered_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind <> 'dataset' OR (repo_id IS NOT NULL AND split IS NOT NULL AND commit IS NOT NULL)",
            name=op.f("ck_dw_mistudio_registrations_dataset_fields"),
        ),
        sa.CheckConstraint(
            "kind <> 'view' OR (probe_dataset_id IS NOT NULL AND role IS NOT NULL AND mapping_sha256 IS NOT NULL AND columns_sha256 IS NOT NULL)",
            name=op.f("ck_dw_mistudio_registrations_view_fields"),
        ),
        sa.CheckConstraint(
            "kind IN ('dataset', 'view')", name=op.f("ck_dw_mistudio_registrations_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["send_id"],
            ["dw_detector_sends.id"],
            name=op.f("fk_dw_mistudio_registrations_send_id_dw_detector_sends"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f("fk_dw_mistudio_registrations_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_mistudio_registrations")),
    )
    op.create_index(
        "ix_dw_mistudio_registrations_probe_dataset",
        "dw_mistudio_registrations",
        ["probe_dataset_id"],
        unique=False,
    )
    op.create_index(
        "uq_dw_mistudio_registrations_dataset",
        "dw_mistudio_registrations",
        [
            "mistudio_base_url",
            "repo_id",
            sa.literal_column("coalesce(config, '')"),
            "split",
            "commit",
        ],
        unique=True,
        postgresql_where=sa.text("kind = 'dataset'"),
    )
    op.create_index(
        "uq_dw_mistudio_registrations_view",
        "dw_mistudio_registrations",
        ["mistudio_base_url", "mistudio_dataset_id", "role", "mapping_sha256", "columns_sha256"],
        unique=True,
        postgresql_where=sa.text("kind = 'view'"),
    )
    op.create_table(
        "dw_reward_marks",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mistudio_base_url", sa.Text(), nullable=False),
        sa.Column("mistudio_probe_id", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("export_id", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("marked_by", sa.Text(), nullable=False),
        sa.Column("marked_by_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "marked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "marked_by_origin IN ('operator', 'agent')",
            name=op.f("ck_dw_reward_marks_origin_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('export', 'operator', 'agent')",
            name=op.f("ck_dw_reward_marks_source_valid"),
        ),
        sa.CheckConstraint(
            "length(marked_by) > 0", name=op.f("ck_dw_reward_marks_marked_by_present")
        ),
        sa.CheckConstraint("length(reason) > 0", name=op.f("ck_dw_reward_marks_reason_present")),
        sa.ForeignKeyConstraint(
            ["export_id"],
            ["dw_exports.id"],
            name=op.f("fk_dw_reward_marks_export_id_dw_exports"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dw_reward_marks")),
        sa.UniqueConstraint(
            "mistudio_base_url", "mistudio_probe_id", name="uq_dw_reward_marks_probe"
        ),
    )
    op.create_table(
        "dw_detector_send_steps",
        sa.Column("send_id", sa.String(length=40), nullable=False),
        sa.Column("step", sa.String(length=16), nullable=False),
        sa.Column("unit_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("publish_id", sa.String(length=40), nullable=True),
        sa.Column("repo_id", sa.Text(), nullable=True),
        sa.Column("commit", sa.String(length=40), nullable=True),
        sa.Column("mistudio_dataset_id", sa.Text(), nullable=True),
        sa.Column("probe_dataset_id", sa.Text(), nullable=True),
        sa.Column("expected_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("registered_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'done', 'reused', 'failed')",
            name=op.f("ck_dw_detector_send_steps_state_valid"),
        ),
        sa.CheckConstraint(
            "step IN ('publish', 'download', 'register')",
            name=op.f("ck_dw_detector_send_steps_step_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["publish_id"],
            ["dw_publishes.id"],
            name=op.f("fk_dw_detector_send_steps_publish_id_dw_publishes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["send_id"],
            ["dw_detector_sends.id"],
            name=op.f("fk_dw_detector_send_steps_send_id_dw_detector_sends"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "send_id", "step", "unit_key", name=op.f("pk_dw_detector_send_steps")
        ),
    )
    op.create_foreign_key(
        op.f("fk_dw_publishes_send_id_dw_detector_sends"),
        "dw_publishes",
        "dw_detector_sends",
        ["send_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(FROZEN)
    op.execute(
        "CREATE TRIGGER dw_detector_sends_frozen BEFORE UPDATE ON dw_detector_sends "
        "FOR EACH ROW EXECUTE FUNCTION dw_detector_sends_frozen()"
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
        )
    op.drop_constraint(op.f("ck_dw_label_runs_kind_valid"), "dw_label_runs", type_="check")
    op.create_check_constraint(op.f("ck_dw_label_runs_kind_valid"), "dw_label_runs", NEW_KINDS)


def downgrade() -> None:
    found = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM dw_label_runs WHERE kind IN ('probe_verdict', 'feature_tag')"
            )
        )
        .scalar_one()
    )
    if found:
        raise RuntimeError(
            f"{found} label runs of kind probe_verdict or feature_tag exist; downgrading would "
            "orphan them. Delete them first (009 FTDD section 11)."
        )
    op.drop_constraint(op.f("ck_dw_label_runs_kind_valid"), "dw_label_runs", type_="check")
    op.create_check_constraint(op.f("ck_dw_label_runs_kind_valid"), "dw_label_runs", OLD_KINDS)
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER {table}_insert_only ON {table}")
    op.execute("DROP TRIGGER dw_detector_sends_frozen ON dw_detector_sends")
    op.execute("DROP FUNCTION dw_detector_sends_frozen()")
    op.drop_constraint(
        op.f("fk_dw_publishes_send_id_dw_detector_sends"), "dw_publishes", type_="foreignkey"
    )
    op.drop_table("dw_detector_send_steps")
    op.drop_table("dw_reward_marks")
    op.drop_index(
        "uq_dw_mistudio_registrations_view",
        table_name="dw_mistudio_registrations",
        postgresql_where=sa.text("kind = 'view'"),
    )
    op.drop_index(
        "uq_dw_mistudio_registrations_dataset",
        table_name="dw_mistudio_registrations",
        postgresql_where=sa.text("kind = 'dataset'"),
    )
    op.drop_index(
        "ix_dw_mistudio_registrations_probe_dataset", table_name="dw_mistudio_registrations"
    )
    op.drop_table("dw_mistudio_registrations")
    op.drop_table("dw_length_profiles")
    op.drop_index(
        "uq_dw_detector_set_roles_single",
        table_name="dw_detector_set_roles",
        postgresql_where=sa.text("role <> 'ood_eval'"),
    )
    op.drop_index("ix_dw_detector_set_roles_version", table_name="dw_detector_set_roles")
    op.drop_table("dw_detector_set_roles")
    op.drop_table("dw_agreement_reports")
    op.drop_index("ix_dw_detector_results_set", table_name="dw_detector_results")
    op.drop_table("dw_detector_results")
    op.drop_index("ix_dw_detector_sends_set", table_name="dw_detector_sends")
    op.drop_table("dw_detector_sends")
    op.drop_table("dw_detector_sets")
