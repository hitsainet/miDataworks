"""dw_sources, dw_source_files, dw_source_annotations (feature 001, FTASKS 2.0).

Lands before feature 002's version tables: 001 FTDD section 4.6 requires this revision to run
before 002's ``dw_version_inputs`` foreign key. CHECK-constrained strings stand in for the native
enum types the FTID names (Foundation's ``dw_jobs`` precedent; see ``models/enums.py``).

Guards, generic over columns so a column added later is guarded by default (001 FTID section 4):
- ``dw_sources``: once ``ready``, only ``state``, ``ready_at``, ``error`` and the ``deleted_*``
  columns may change; transitions ``importing -> ready|failed|cancelled`` and ``ready -> deleted``
  only; physical deletes refused (tombstones).
- ``dw_source_files`` and ``dw_source_annotations``: append-only.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SOURCES_GUARD = """
CREATE FUNCTION dw_sources_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  permitted text[] := ARRAY['state', 'ready_at', 'error', 'deleted_by', 'deleted_by_origin',
                            'deleted_at'];
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

APPEND_ONLY = """
CREATE FUNCTION dw_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END;
$$;
"""

UNRESERVED = """
CREATE FUNCTION dw_columns_unreserved(cols jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(cols) AS e WHERE left(e->>'name', 4) = '_dw_'
  );
$$;
"""


def upgrade() -> None:
    op.execute(UNRESERVED)
    op.execute(SOURCES_GUARD)
    op.execute(APPEND_ONLY)
    op.create_table('dw_sources',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('repo_id', sa.Text(), nullable=True),
    sa.Column('config', sa.Text(), nullable=True),
    sa.Column('split_selection', sa.Text(), nullable=True),
    sa.Column('requested_ref', sa.Text(), nullable=True),
    sa.Column('resolved_commit', sa.String(length=40), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('parse_options', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('licence_raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('licence_display', sa.Text(), nullable=False),
    sa.Column('licence_origin', sa.String(length=16), nullable=True),
    sa.Column('gated', sa.String(length=16), nullable=True),
    sa.Column('token_tier', sa.String(length=16), nullable=True),
    sa.Column('detection', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('library_versions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('import_job_id', sa.String(length=40), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('deleted_by', sa.Text(), nullable=True),
    sa.Column('deleted_by_origin', sa.String(length=16), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(kind = 'hf' AND repo_id IS NOT NULL AND content_hash IS NULL) OR (kind = 'upload' AND repo_id IS NULL AND resolved_commit IS NULL)", name=op.f('ck_dw_sources_kind_identity')),
    sa.CheckConstraint("content_hash IS NULL OR content_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_sources_content_hash_pattern')),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_sources_origin_valid')),
    sa.CheckConstraint("deleted_by_origin IS NULL OR deleted_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_sources_deleted_origin_valid')),
    sa.CheckConstraint("kind IN ('hf', 'upload')", name=op.f('ck_dw_sources_kind_valid')),
    sa.CheckConstraint("resolved_commit IS NULL OR resolved_commit ~ '^[0-9a-f]{40}$'", name=op.f('ck_dw_sources_commit_pattern')),
    sa.CheckConstraint("state <> 'ready' OR (kind = 'hf' AND resolved_commit IS NOT NULL) OR (kind = 'upload' AND content_hash IS NOT NULL)", name=op.f('ck_dw_sources_ready_is_pinned')),
    sa.CheckConstraint("state IN ('importing', 'ready', 'failed', 'cancelled', 'deleted')", name=op.f('ck_dw_sources_state_valid')),
    sa.CheckConstraint("token_tier IS NULL OR token_tier IN ('per_import', 'stored', 'none')", name=op.f('ck_dw_sources_token_tier_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_sources_created_by_present')),
    sa.ForeignKeyConstraint(['import_job_id'], ['dw_jobs.id'], name=op.f('fk_dw_sources_import_job_id_dw_jobs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_sources'))
    )
    op.create_index('ix_dw_sources_state_created', 'dw_sources', ['state', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('uq_dw_sources_hf_identity', 'dw_sources', ['repo_id', sa.literal_column("coalesce(config, '')"), sa.literal_column("coalesce(split_selection, '')"), 'resolved_commit'], unique=True, postgresql_where=sa.text("kind = 'hf' AND state IN ('importing', 'ready')"))
    op.create_index('uq_dw_sources_upload_identity', 'dw_sources', ['content_hash'], unique=True, postgresql_where=sa.text("kind = 'upload' AND state IN ('importing', 'ready')"))
    op.create_table('dw_source_files',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('split', sa.Text(), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('rows', sa.BigInteger(), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('original_name', sa.Text(), nullable=True),
    sa.Column('original_sha256', sa.String(length=64), nullable=True),
    sa.Column('original_bytes', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("original_sha256 IS NULL OR original_sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_source_files_original_sha256_pattern')),
    sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f('ck_dw_source_files_sha256_pattern')),
    sa.CheckConstraint('dw_columns_unreserved(columns)', name=op.f('ck_dw_source_files_no_reserved_columns')),
    sa.CheckConstraint('rows >= 0 AND bytes >= 0', name=op.f('ck_dw_source_files_counts_nonnegative')),
    sa.ForeignKeyConstraint(['source_id'], ['dw_sources.id'], name=op.f('fk_dw_source_files_source_id_dw_sources'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_source_files')),
    sa.UniqueConstraint('source_id', 'split', name='uq_dw_source_files_source_split')
    )
    op.create_table('dw_source_annotations',
    sa.Column('id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('source_id', sa.Uuid(as_uuid=False), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('redistribution', sa.String(length=16), nullable=True),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_by', sa.Text(), nullable=False),
    sa.Column('created_by_origin', sa.String(length=16), nullable=False),
    sa.Column('approval_id', sa.String(length=40), nullable=True),
    sa.Column('approved_by', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(created_by_origin = 'agent' AND approval_id IS NOT NULL AND approved_by IS NOT NULL) OR (created_by_origin = 'operator' AND approval_id IS NULL AND approved_by IS NULL)", name=op.f('ck_dw_source_annotations_agent_needs_approval')),
    sa.CheckConstraint("created_by_origin IN ('operator', 'agent')", name=op.f('ck_dw_source_annotations_origin_valid')),
    sa.CheckConstraint("kind = 'detection_override' OR redistribution IS NOT NULL", name=op.f('ck_dw_source_annotations_redistribution_required')),
    sa.CheckConstraint("kind IN ('terms', 'licence', 'detection_override')", name=op.f('ck_dw_source_annotations_kind_valid')),
    sa.CheckConstraint("redistribution IS NULL OR redistribution IN ('permits', 'private_only', 'forbids')", name=op.f('ck_dw_source_annotations_redistribution_valid')),
    sa.CheckConstraint('length(created_by) > 0', name=op.f('ck_dw_source_annotations_created_by_present')),
    sa.CheckConstraint('length(reason) > 0', name=op.f('ck_dw_source_annotations_reason_present')),
    sa.ForeignKeyConstraint(['approval_id'], ['dw_approvals.id'], name=op.f('fk_dw_source_annotations_approval_id_dw_approvals'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_id'], ['dw_sources.id'], name=op.f('fk_dw_source_annotations_source_id_dw_sources'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dw_source_annotations'))
    )
    op.create_index('ix_dw_source_annotations_source_kind', 'dw_source_annotations', ['source_id', 'kind', 'created_at'], unique=False)
    op.execute(
        "CREATE TRIGGER dw_sources_guard BEFORE UPDATE OR DELETE ON dw_sources "
        "FOR EACH ROW EXECUTE FUNCTION dw_sources_guard()"
    )
    for table in ("dw_source_files", "dw_source_annotations"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
        )


def downgrade() -> None:
    op.drop_table("dw_source_annotations")
    op.drop_table("dw_source_files")
    op.drop_table("dw_sources")
    op.execute("DROP FUNCTION dw_append_only()")
    op.execute("DROP FUNCTION dw_sources_guard()")
    op.execute("DROP FUNCTION dw_columns_unreserved(jsonb)")
