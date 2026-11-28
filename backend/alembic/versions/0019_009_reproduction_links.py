"""Feature 009: reproduction links (FR-009.77, option (b) of the operator decision of 2026-10-07).

``dw_reproduction_links``: one row per link between a miDataworks version split and an evaluation
miStudio already recorded for a probe, with the checks that were run when it was made. The
reproduction gate may use a link as its target when no results snapshot describes the probe (an
imported probe whose training data miDataworks never sent).

- ``BEFORE UPDATE``: refused (``dw_append_only()``, migration 0005). A link is evidence.
- ``BEFORE DELETE``: refused while any label run names the link in
  ``endpoint_snapshot -> 'reproduction' ->> 'link_id'`` (``dw_reproduction_link_unused``). The
  service refuses first with a readable message; the trigger is the floor.

Downgrade refuses while a label run names a link: dropping the table would orphan the evidence the
run's reproduction record points at.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
TABLE = "dw_reproduction_links"

UNUSED = """
CREATE FUNCTION dw_reproduction_link_unused() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM dw_label_runs
    WHERE endpoint_snapshot -> 'reproduction' ->> 'link_id' = OLD.id
  ) THEN
    RAISE EXCEPTION 'dw_reproduction_links: link % is named by a label run and is immutable evidence', OLD.id;
  END IF;
  RETURN OLD;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("mistudio_base_url", sa.Text(), nullable=False),
        sa.Column("mistudio_probe_id", sa.Text(), nullable=False),
        sa.Column("mistudio_run_id", sa.Text(), nullable=True),
        sa.Column("probe_dataset_id", sa.Text(), nullable=False),
        sa.Column("evaluation_id", sa.Text(), nullable=True),
        sa.Column("view_name", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("version_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("split", sa.Text(), nullable=False),
        sa.Column("input_column", sa.Text(), nullable=False),
        sa.Column("label_column", sa.Text(), nullable=False),
        sa.Column("label_mapping", JSONB, nullable=False),
        sa.Column("auroc", sa.Float(), nullable=False),
        sa.Column("ci_low", sa.Float(), nullable=False),
        sa.Column("ci_high", sa.Float(), nullable=False),
        sa.Column("n_positive", sa.Integer(), nullable=False),
        sa.Column("n_negative", sa.Integer(), nullable=False),
        sa.Column("check_level", sa.String(length=16), nullable=False),
        sa.Column("checks", JSONB, nullable=False),
        sa.Column("scoring_form", JSONB, nullable=False),
        sa.Column("evidence", JSONB, nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(length=16), nullable=False),
        sa.Column("approval_id", sa.String(length=40), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "check_level IN ('content', 'counts_only')", name=op.f(f"ck_{TABLE}_check_level_valid")
        ),
        sa.CheckConstraint("role IN ('id_test', 'ood_eval')", name=op.f(f"ck_{TABLE}_role_valid")),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')", name=op.f(f"ck_{TABLE}_origin_valid")
        ),
        sa.CheckConstraint("length(created_by) > 0", name=op.f(f"ck_{TABLE}_created_by_present")),
        sa.CheckConstraint(
            "(created_by_origin = 'agent') = (approval_id IS NOT NULL AND approved_by IS NOT NULL)",
            name=op.f(f"ck_{TABLE}_agent_iff_approved"),
        ),
        sa.CheckConstraint(
            "ci_low <= auroc AND auroc <= ci_high", name=op.f(f"ck_{TABLE}_interval_holds_auroc")
        ),
        sa.CheckConstraint(
            "n_positive > 0 AND n_negative > 0", name=op.f(f"ck_{TABLE}_both_classes")
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["dw_versions.id"],
            name=op.f(f"fk_{TABLE}_version_id_dw_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["dw_approvals.id"],
            name=op.f(f"fk_{TABLE}_approval_id_dw_approvals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint(
            "mistudio_base_url",
            "mistudio_probe_id",
            "probe_dataset_id",
            "version_id",
            "split",
            name="uq_dw_reproduction_links_target",
        ),
    )
    op.create_index(
        "ix_dw_reproduction_links_probe", TABLE, ["mistudio_probe_id", "created_at"], unique=False
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_frozen BEFORE UPDATE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
    )
    op.execute(UNUSED)
    op.execute(
        f"CREATE TRIGGER {TABLE}_unused BEFORE DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION dw_reproduction_link_unused()"
    )


def downgrade() -> None:
    named = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM dw_label_runs "
                "WHERE endpoint_snapshot -> 'reproduction' ->> 'link_id' IS NOT NULL"
            )
        )
        .scalar_one()
    )
    if named:
        raise RuntimeError(
            f"{named} label run(s) name a reproduction link; downgrading would orphan the evidence "
            "their reproduction records point at."
        )
    op.execute(f"DROP TRIGGER {TABLE}_unused ON {TABLE}")
    op.execute(f"DROP TRIGGER {TABLE}_frozen ON {TABLE}")
    op.execute("DROP FUNCTION dw_reproduction_link_unused()")
    op.drop_index("ix_dw_reproduction_links_probe", table_name=TABLE)
    op.drop_table(TABLE)
