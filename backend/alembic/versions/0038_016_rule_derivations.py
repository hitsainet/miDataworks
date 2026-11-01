"""Feature 016 slice 4: rules derived from a labelled version (FTID 016 section 4.3).

1. ``dw_rule_derivations`` (new): one derivation job's inputs (with the settings in force, so a
   later setting change never changes a stored result), state, figures and draft (the RFC 8785 text
   and its sha256, which the database CHECKs). A guard trigger refuses any UPDATE of a row whose
   status is terminal and any DELETE outside ``dw.purge`` (the pattern of ``dw_publishes_guard``).
2. ``dw_version_inputs``: kind ``rule_source`` (a version whose build ran a rule set derived from
   another version records that version as an ANCESTOR, so its sources reach the manifest and C-1
   applies the source's visibility floor; FR-016.31). It references a version, like ``version``.

``downgrade`` refuses while a ``rule_source`` input exists (a version's provenance record).

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

T = "dw_rule_derivations"
INPUTS = "dw_version_inputs"
TERMINAL = "('completed', 'failed', 'cancelled')"
OLD_REFERENCE = (
    "(kind = 'source' AND source_id IS NOT NULL AND input_version_id IS NULL) OR "
    "(kind = 'version' AND input_version_id IS NOT NULL AND source_id IS NULL)"
)
NEW_REFERENCE = (
    "(kind = 'source' AND source_id IS NOT NULL AND input_version_id IS NULL) OR "
    "(kind IN ('version', 'rule_source') AND input_version_id IS NOT NULL AND source_id IS NULL)"
)

GUARD = f"""
CREATE FUNCTION dw_rule_derivations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF current_setting('dw.purge', true) IS DISTINCT FROM 'on' THEN
      RAISE EXCEPTION 'dw_rule_derivations rows are deleted only by a purge (feature 016)';
    END IF;
    RETURN OLD;
  END IF;
  IF OLD.status IN {TERMINAL} THEN
    RAISE EXCEPTION 'rule derivation % is terminal (%) and never changes', OLD.id, OLD.status;
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        T,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("job_id", sa.String(40), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("params_sha256", sa.String(64), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("method_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16)),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("draft_canonical", sa.Text()),
        sa.Column("draft_sha256", sa.String(64)),
        sa.Column("inherited_licence_classes", postgresql.JSONB()),
        sa.Column("error", postgresql.JSONB()),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name=op.f(f"ck_{T}_status_valid"),
        ),
        sa.CheckConstraint(
            "state IS NULL OR state IN ('measured', 'low_coverage', 'not_measured')",
            name=op.f(f"ck_{T}_state_valid"),
        ),
        sa.CheckConstraint("params_sha256 ~ '^[0-9a-f]{64}$'", name=op.f(f"ck_{T}_params_sha256")),
        sa.CheckConstraint(
            "(draft_canonical IS NULL AND draft_sha256 IS NULL) OR (draft_canonical IS NOT NULL "
            "AND encode(sha256(convert_to(draft_canonical, 'UTF8')), 'hex') = draft_sha256)",
            name=op.f(f"ck_{T}_draft_sha"),
        ),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')", name=op.f(f"ck_{T}_origin_valid")
        ),
        sa.CheckConstraint("length(created_by) > 0", name=op.f(f"ck_{T}_created_by_present")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["dw_jobs.id"], name=op.f(f"fk_{T}_job_id_dw_jobs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["dw_versions.id"],
            name=op.f(f"fk_{T}_source_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{T}")),
    )
    op.create_index("ix_dw_rule_derivations_source", T, ["source_version_id", "created_at"])
    op.execute(GUARD)
    op.execute(
        f"CREATE TRIGGER {T}_guard BEFORE UPDATE OR DELETE ON {T} "
        "FOR EACH ROW EXECUTE FUNCTION dw_rule_derivations_guard()"
    )
    op.drop_constraint(op.f(f"ck_{INPUTS}_kind_valid"), INPUTS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{INPUTS}_kind_valid"), INPUTS, "kind IN ('source', 'version', 'rule_source')"
    )
    op.drop_constraint(op.f(f"ck_{INPUTS}_exactly_one_reference"), INPUTS, type_="check")
    op.create_check_constraint(op.f(f"ck_{INPUTS}_exactly_one_reference"), INPUTS, NEW_REFERENCE)


def downgrade() -> None:
    bind = op.get_bind()
    held = bind.execute(
        sa.text(f"SELECT count(*) FROM {INPUTS} WHERE kind = 'rule_source'")  # noqa: S608
    ).scalar_one()
    if held:
        raise RuntimeError(
            f"refusing to downgrade 0038: {held} version input(s) of kind rule_source record "
            "where a version's rules were learned"
        )
    op.drop_constraint(op.f(f"ck_{INPUTS}_exactly_one_reference"), INPUTS, type_="check")
    op.create_check_constraint(op.f(f"ck_{INPUTS}_exactly_one_reference"), INPUTS, OLD_REFERENCE)
    op.drop_constraint(op.f(f"ck_{INPUTS}_kind_valid"), INPUTS, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{INPUTS}_kind_valid"), INPUTS, "kind IN ('source', 'version')"
    )
    op.execute(f"DROP TRIGGER {T}_guard ON {T}")
    op.execute("DROP FUNCTION dw_rule_derivations_guard()")
    op.drop_index("ix_dw_rule_derivations_source", table_name=T)
    op.drop_table(T)
