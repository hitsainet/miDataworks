"""Feature 009: minimal pairs as a chain (FR-009.60 - FR-009.64; operator decision 2026-10-07).

- ``dw_minimal_pair_chains``: one row per chain, naming each stage's run, job and version, so the
  operator can see every stage and resume the one that stopped.
- Through its owner (007): ``dw_generation_runs`` gains mode ``minimal_pairs`` and target type
  ``detector``, with two shape rules: a minimal-pair run makes one counterpart per seed into a
  ``detector`` dataset, and a ``detector`` run is only ever a minimal-pair run.

Downgrade refuses while a ``minimal_pairs`` generation run exists: dropping the values from the
CHECKs would orphan it.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLE = "dw_generation_runs"
OLD_MODES = "mode IN ('standard', 'steered_pairs')"
NEW_MODES = "mode IN ('standard', 'steered_pairs', 'minimal_pairs')"
OLD_TARGETS = "target_type IN ('sft', 'kto', 'grpo_prompt', 'dpo')"
NEW_TARGETS = "target_type IN ('sft', 'kto', 'grpo_prompt', 'dpo', 'detector')"
MINIMAL_SHAPE = (
    "mode <> 'minimal_pairs' OR (n_responses = 1 AND chosen_side IS NULL "
    "AND target_type = 'detector')"
)
DETECTOR_ONLY_MINIMAL = "target_type <> 'detector' OR mode = 'minimal_pairs'"


def _replace(name: str, condition: str) -> None:
    op.drop_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, type_="check")
    op.create_check_constraint(op.f(f"ck_{TABLE}_{name}"), TABLE, condition)


def upgrade() -> None:
    _replace("mode_valid", NEW_MODES)
    _replace("target_valid", NEW_TARGETS)
    op.create_check_constraint(op.f(f"ck_{TABLE}_minimal_pairs_shape"), TABLE, MINIMAL_SHAPE)
    op.create_check_constraint(
        op.f(f"ck_{TABLE}_detector_is_minimal_pairs"), TABLE, DETECTOR_ONLY_MINIMAL
    )
    op.create_table(
        "dw_minimal_pair_chains",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("input_version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("generation_run_id", sa.String(length=40), nullable=False),
        sa.Column("scope_job_id", sa.String(length=40), nullable=True),
        sa.Column("scope_version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("judge_run_id", sa.String(length=40), nullable=True),
        sa.Column("pair_job_id", sa.String(length=40), nullable=True),
        sa.Column("pair_version_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("failed_stage", sa.String(length=16), nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("counts", JSONB, nullable=False),
        sa.Column("started_by", sa.Text(), nullable=False),
        sa.Column("started_by_origin", sa.String(length=16), nullable=False),
        sa.Column("acting_by", sa.Text(), nullable=False),
        sa.Column("acting_origin", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'failed', 'cancelled')", name="state_valid"
        ),
        sa.CheckConstraint(
            "stage IN ('generate', 'scope', 'judge', 'pair', 'done')", name="stage_valid"
        ),
        sa.CheckConstraint(
            "failed_stage IS NULL OR failed_stage IN ('generate', 'scope', 'judge', 'pair')",
            name="failed_stage_valid",
        ),
        sa.CheckConstraint("state <> 'failed' OR failed_stage IS NOT NULL", name="failure_named"),
        sa.CheckConstraint(
            "state <> 'completed' OR (stage = 'done' AND pair_version_id IS NOT NULL)",
            name="completed_has_pairs",
        ),
        sa.CheckConstraint("started_by_origin IN ('operator', 'agent')", name="origin_valid"),
        sa.CheckConstraint("acting_origin IN ('operator', 'agent')", name="acting_origin_valid"),
        sa.CheckConstraint("length(started_by) > 0", name="started_by_present"),
        sa.ForeignKeyConstraint(["input_version_id"], ["dw_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["generation_run_id"], ["dw_generation_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["scope_version_id"], ["dw_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["judge_run_id"], ["dw_label_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pair_version_id"], ["dw_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dw_minimal_pair_chains_state", "dw_minimal_pair_chains", ["state", "updated_at"]
    )
    op.create_index(
        "ix_dw_minimal_pair_chains_version",
        "dw_minimal_pair_chains",
        ["input_version_id", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    found = bind.execute(
        sa.text("SELECT count(*) FROM dw_generation_runs WHERE mode = 'minimal_pairs'")
    ).scalar_one()
    if found:
        raise RuntimeError(
            f"{found} minimal_pairs generation run(s) exist; dropping the mode would orphan them."
        )
    op.drop_index("ix_dw_minimal_pair_chains_version", table_name="dw_minimal_pair_chains")
    op.drop_index("ix_dw_minimal_pair_chains_state", table_name="dw_minimal_pair_chains")
    op.drop_table("dw_minimal_pair_chains")
    op.drop_constraint(op.f(f"ck_{TABLE}_detector_is_minimal_pairs"), TABLE, type_="check")
    op.drop_constraint(op.f(f"ck_{TABLE}_minimal_pairs_shape"), TABLE, type_="check")
    _replace("target_valid", OLD_TARGETS)
    _replace("mode_valid", OLD_MODES)
