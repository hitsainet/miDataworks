"""Feature 002 tables: datasets, recipes, step executions, versions (002 FTASKS 3.2).

Row events (partitioned) are revision 0007; the immutability, insert-only and manifest-hash
triggers are revision 0008. ``dw_version_inputs.source_id`` references feature 001's
``dw_sources`` with ``ON DELETE RESTRICT`` (revision 0005 runs first, 001 FTDD section 4.6).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('dw_datasets',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('target_type', sa.String(length=16), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('next_version_number', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_datasets_origin_valid')),
    sa.CheckConstraint("name ~ '^[a-z0-9][a-z0-9-]{0,99}$'", name=op.f('ck_dw_datasets_name_pattern')),
    sa.CheckConstraint("target_type IN ('sft', 'dpo', 'kto', 'grpo_prompt', 'prm', 'detector', 'untyped')", name=op.f('ck_dw_datasets_target_type_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_datasets_created_by_present')),
    sa.CheckConstraint('next_version_number >= 1', name=op.f('ck_dw_datasets_next_number_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_datasets')),
    sa.UniqueConstraint('name', name=op.f('uq_dw_datasets_name'))
    )
    op.create_table('dw_recipe_bodies',
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('canonical', sa.LargeBinary(), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_recipe_bodies_hash_pattern')),
    sa.PrimaryKeyConstraint('hash', name=op.f('pk_dw_recipe_bodies'))
    )
    op.create_table('dw_recipes',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('archived_by', sa.Text(), nullable=True),
    sa.Column('archived_by_origin', sa.String(length=16), nullable=True),
    sa.Column('head_revision_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("archived_by_origin IS NULL OR archived_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_recipes_archived_origin_valid')),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_recipes_origin_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_recipes_created_by_present')),
    sa.CheckConstraint('length(name) > 0', name=op.f('ck_dw_recipes_name_present')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_recipes')),
    sa.UniqueConstraint('name', name=op.f('uq_dw_recipes_name'))
    )
    op.create_table('dw_recipe_revisions',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('recipe_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('revision_number', sa.Integer(), nullable=False),
    sa.Column('recipe_hash', sa.String(length=64), nullable=False),
    sa.Column('step_labels', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('cloned_from_revision_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('imported', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_recipe_revisions_origin_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_recipe_revisions_created_by_present')),
    sa.CheckConstraint('revision_number >= 1', name=op.f('ck_dw_recipe_revisions_number_positive')),
    sa.ForeignKeyConstraint(['cloned_from_revision_id'], ['dw_recipe_revisions.id'], name=op.f('fk_dw_recipe_revisions_cloned_from_revision_id_dw_recipe_revisions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipe_hash'], ['dw_recipe_bodies.hash'], name=op.f('fk_dw_recipe_revisions_recipe_hash_dw_recipe_bodies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipe_id'], ['dw_recipes.id'], name=op.f('fk_dw_recipe_revisions_recipe_id_dw_recipes'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_recipe_revisions')),
    sa.UniqueConstraint('recipe_id', 'revision_number', name='uq_dw_recipe_revisions_number')
    )
    op.create_foreign_key(
        op.f("fk_dw_recipes_head_revision_id_dw_recipe_revisions"),
        "dw_recipes",
        "dw_recipe_revisions",
        ["head_revision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table('dw_recipe_drafts',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('recipe_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('dataset_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=True),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('step_labels', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('flow_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_by', sa.Text(), nullable=False),
    sa.Column('updated_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("updated_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_recipe_drafts_origin_valid')),
    sa.CheckConstraint('length(updated_by) > 0', name=op.f('ck_dw_recipe_drafts_updated_by_present')),
    sa.ForeignKeyConstraint(['dataset_id'], ['dw_datasets.id'], name=op.f('fk_dw_recipe_drafts_dataset_id_dw_datasets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['recipe_id'], ['dw_recipes.id'], name=op.f('fk_dw_recipe_drafts_recipe_id_dw_recipes'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_recipe_drafts'))
    )
    op.create_table('dw_step_executions',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('identity_digest', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('operator_name', sa.String(length=64), nullable=True),
    sa.Column('operator_version', sa.String(length=64), nullable=True),
    sa.Column('manifest_hash', sa.String(length=64), nullable=True),
    sa.Column('params_hash', sa.String(length=64), nullable=True),
    sa.Column('step_seed', sa.BigInteger(), nullable=True),
    sa.Column('bindings_digest', sa.String(length=64), nullable=True),
    sa.Column('input_execution_digest', sa.String(length=64), nullable=True),
    sa.Column('input_logical_digest', sa.String(length=64), nullable=True),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('output_dir', sa.Text(), nullable=False),
    sa.Column('output_logical_digest', sa.String(length=64), nullable=True),
    sa.Column('output_column_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('split_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('rows_in', sa.BigInteger(), nullable=True),
    sa.Column('rows_kept', sa.BigInteger(), nullable=True),
    sa.Column('rows_changed', sa.BigInteger(), nullable=True),
    sa.Column('rows_dropped', sa.BigInteger(), nullable=True),
    sa.Column('rows_added', sa.BigInteger(), nullable=True),
    sa.Column('rows_split_assigned', sa.BigInteger(), nullable=True),
    sa.Column('reason_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('job_id', sa.String(length=40), nullable=False),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(kind = 'assemble' AND operator_name IS NULL) OR (kind = 'operator' AND operator_name IS NOT NULL AND operator_version IS NOT NULL AND manifest_hash IS NOT NULL)", name=op.f('ck_dw_step_executions_operator_fields')),
    sa.CheckConstraint("identity_digest ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_step_executions_identity_pattern')),
    sa.CheckConstraint("kind IN ('assemble', 'operator')", name=op.f('ck_dw_step_executions_kind_valid')),
    sa.CheckConstraint("state IN ('queued', 'running', 'completed', 'failed', 'cancelled')", name=op.f('ck_dw_step_executions_state_valid')),
    sa.ForeignKeyConstraint(['job_id'], ['dw_jobs.id'], name=op.f('fk_dw_step_executions_job_id_dw_jobs'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_step_executions'))
    )
    op.create_index('ix_dw_step_executions_job', 'dw_step_executions', ['job_id'], unique=False)
    op.create_index('uq_dw_step_executions_identity_live', 'dw_step_executions', ['identity_digest'], unique=True, postgresql_where=sa.text("state IN ('queued', 'running', 'completed')"))
    op.create_table('dw_versions',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('dataset_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('parent_version_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('request_digest', sa.String(length=64), nullable=False),
    sa.Column('inputs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('recipe_hash', sa.String(length=64), nullable=False),
    sa.Column('recipe_revision_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('seed', sa.BigInteger(), nullable=False),
    sa.Column('bindings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('rowkey_scheme', sa.String(length=32), nullable=False),
    sa.Column('column_roles', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('splits', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_rows', sa.BigInteger(), nullable=False),
    sa.Column('total_bytes', sa.BigInteger(), nullable=False),
    sa.Column('held_out_origin_version_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('warnings', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('drop_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('manifest', sa.LargeBinary(), nullable=False),
    sa.Column('manifest_sha256', sa.String(length=64), nullable=False),
    sa.Column('build_job_id', sa.String(length=40), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_by', sa.Text(), nullable=True),
    sa.Column('deleted_by_origin', sa.String(length=16), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delete_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("(state = 'completed' AND deleted_at IS NULL AND deleted_by IS NULL) OR (state = 'deleted' AND deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND deleted_by_origin IS NOT NULL)", name=op.f('ck_dw_versions_tombstone_consistent')),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_versions_origin_valid')),
    sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_versions_manifest_sha256_pattern')),
    sa.CheckConstraint("request_digest ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_versions_request_digest_pattern')),
    sa.CheckConstraint("state IN ('completed', 'deleted')", name=op.f('ck_dw_versions_state_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_versions_created_by_present')),
    sa.CheckConstraint('number >= 1', name=op.f('ck_dw_versions_number_positive')),
    sa.CheckConstraint('seed >= 0 AND seed < 2147483648', name=op.f('ck_dw_versions_seed_range')),
    sa.ForeignKeyConstraint(['build_job_id'], ['dw_jobs.id'], name=op.f('fk_dw_versions_build_job_id_dw_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['dataset_id'], ['dw_datasets.id'], name=op.f('fk_dw_versions_dataset_id_dw_datasets'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['held_out_origin_version_id'], ['dw_versions.id'], name=op.f('fk_dw_versions_held_out_origin_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_version_id'], ['dw_versions.id'], name=op.f('fk_dw_versions_parent_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipe_hash'], ['dw_recipe_bodies.hash'], name=op.f('fk_dw_versions_recipe_hash_dw_recipe_bodies'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipe_revision_id'], ['dw_recipe_revisions.id'], name=op.f('fk_dw_versions_recipe_revision_id_dw_recipe_revisions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_versions')),
    sa.UniqueConstraint('dataset_id', 'number', name='uq_dw_versions_dataset_number')
    )
    op.create_index('ix_dw_versions_parent', 'dw_versions', ['parent_version_id'], unique=False)
    op.create_index('uq_dw_versions_request_digest_completed', 'dw_versions', ['request_digest'], unique=True, postgresql_where=sa.text("state = 'completed'"))
    op.create_table('dw_version_builds',
    sa.Column('job_id', sa.String(length=40), nullable=False),
    sa.Column('dataset_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('request_digest', sa.String(length=64), nullable=False),
    sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reuse_enabled', sa.Boolean(), nullable=False),
    sa.Column('verify_version_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('sources_verified', sa.Boolean(), nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('current_step_index', sa.Integer(), nullable=False),
    sa.Column('waiting_execution_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['dw_datasets.id'], name=op.f('fk_dw_version_builds_dataset_id_dw_datasets'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['job_id'], ['dw_jobs.id'], name=op.f('fk_dw_version_builds_job_id_dw_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['verify_version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_builds_verify_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_builds_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['waiting_execution_id'], ['dw_step_executions.id'], name=op.f('fk_dw_version_builds_waiting_execution_id_dw_step_executions'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('job_id', name=op.f('pk_dw_version_builds'))
    )
    op.create_index('ix_dw_version_builds_digest', 'dw_version_builds', ['request_digest'], unique=False)
    op.create_index('ix_dw_version_builds_waiting', 'dw_version_builds', ['waiting_execution_id'], unique=False)
    op.create_table('dw_version_comparisons',
    sa.Column('version_a', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('version_b', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['version_a'], ['dw_versions.id'], name=op.f('fk_dw_version_comparisons_version_a_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_b'], ['dw_versions.id'], name=op.f('fk_dw_version_comparisons_version_b_dw_versions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('version_a', 'version_b', name=op.f('pk_dw_version_comparisons'))
    )
    op.create_table('dw_version_inputs',
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.Column('input_version_id', sa.Uuid(as_uuid=False), nullable=True),
    sa.CheckConstraint("(kind = 'source' AND source_id IS NOT NULL AND input_version_id IS NULL) OR (kind = 'version' AND input_version_id IS NOT NULL AND source_id IS NULL)", name=op.f('ck_dw_version_inputs_exactly_one_reference')),
    sa.CheckConstraint("kind IN ('source', 'version')", name=op.f('ck_dw_version_inputs_kind_valid')),
    sa.ForeignKeyConstraint(['input_version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_inputs_input_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['dw_sources.id'], name=op.f('fk_dw_version_inputs_source_id_dw_sources'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_inputs_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('version_id', 'position', name=op.f('pk_dw_version_inputs'))
    )
    op.create_index('ix_dw_version_inputs_input_version', 'dw_version_inputs', ['input_version_id'], unique=False)
    op.create_index('ix_dw_version_inputs_source', 'dw_version_inputs', ['source_id'], unique=False)
    op.create_table('dw_version_steps',
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('step_index', sa.SmallInteger(), nullable=False),
    sa.Column('step_execution_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('reused', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['step_execution_id'], ['dw_step_executions.id'], name=op.f('fk_dw_version_steps_step_execution_id_dw_step_executions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_steps_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('version_id', 'step_index', name=op.f('pk_dw_version_steps'))
    )
    op.create_index('ix_dw_version_steps_execution', 'dw_version_steps', ['step_execution_id'], unique=False)
    op.create_table('dw_version_verifications',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('version_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('job_id', sa.String(length=40), nullable=False),
    sa.Column('result', sa.String(length=16), nullable=False),
    sa.Column('splits', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('first_mismatch', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_version_verifications_origin_valid')),
    sa.CheckConstraint("result IN ('match', 'mismatch', 'failed')", name=op.f('ck_dw_version_verifications_result_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_version_verifications_created_by_present')),
    sa.ForeignKeyConstraint(['job_id'], ['dw_jobs.id'], name=op.f('fk_dw_version_verifications_job_id_dw_jobs'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['version_id'], ['dw_versions.id'], name=op.f('fk_dw_version_verifications_version_id_dw_versions'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_version_verifications'))
    )
    op.create_index('ix_dw_version_verifications_version', 'dw_version_verifications', ['version_id'], unique=False)


def downgrade() -> None:
    for table in (
        "dw_version_verifications",
        "dw_version_steps",
        "dw_version_inputs",
        "dw_version_comparisons",
        "dw_version_builds",
        "dw_versions",
        "dw_step_executions",
        "dw_recipe_drafts",
    ):
        op.drop_table(table)
    op.drop_constraint(
        op.f("fk_dw_recipes_head_revision_id_dw_recipe_revisions"), "dw_recipes", type_="foreignkey"
    )
    for table in ("dw_recipe_revisions", "dw_recipes", "dw_recipe_bodies", "dw_datasets"):
        op.drop_table(table)
