"""Feature 018 slice 3: operator code rules in the versioned configuration store (FTID 018 §4.1).

1. ``dw_code_rules`` (new): the head row of a code-rule name - its one behaviour key and the four
   archive columns. Not insert-only: archiving is an UPDATE of this row.
2. ``dw_config_versions``: kind ``code_rule`` admitted; a ``code_rule`` row carries its canonical
   document text (016's ``canonical_sha`` CHECK then proves ``body_sha256`` = the approved
   ``code_sha256``) and ``plugin = 'code_rule'``; a ``code_rule`` row needs its head (trigger
   ``dw_config_versions_code_rule_head``). 016's split insert-only trigger stays as it is.
3. ``dw_code_rule_approvals`` (new): the durable per-hash approval list (FR-018.29) - append-only,
   ``origin`` CHECKed to ``'operator'`` (no agent ever approves, P-09), the approval id of an
   approved agent request as a RESTRICT foreign key. Never updated, never deleted.
4. ``dw_code_rule_contract_runs`` (new): each hash's cases under each runner contract version
   (FR-018.31). Never updated, never deleted.
5. ``dw_archive_events`` admits the entity kind ``code_rule`` (Discrepancy X8). ``dw_purge_events``
   does not change: a code rule is not purgeable until slice 4 brings its build records.

``downgrade`` refuses while an approval row or a ``code_rule`` archive record exists (they are the
operator's decisions; the restored CHECK would not admit the latter), then drops everything and
restores the original CHECK. Literals are written out (a migration never imports app code);
``tests/integration/code_rules/test_migration_0043.py`` pins them to the models.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEADS = "dw_code_rules"
APPROVALS = "dw_code_rule_approvals"
RUNS = "dw_code_rule_contract_runs"
CONFIG = "dw_config_versions"
NAME = "^[a-z0-9][a-z0-9-]{0,63}$"
SLUG = "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
SHA = "~ '^[0-9a-f]{64}$'"
ARCHIVED_CONSISTENT = (
    "(archived_at IS NULL AND archived_by IS NULL AND archived_by_origin IS NULL "
    "AND archive_reason IS NULL) OR "
    "(archived_at IS NOT NULL AND archived_by IS NOT NULL AND length(archived_by) > 0 "
    "AND archived_by_origin IN ('operator', 'agent'))"
)
OLD_CONFIG_KINDS = "kind IN ('selector', 'grader', 'rule_set')"
NEW_CONFIG_KINDS = "kind IN ('selector', 'grader', 'rule_set', 'code_rule')"
CODE_RULE_CANONICAL = "kind <> 'code_rule' OR (body_canonical IS NOT NULL AND plugin = 'code_rule')"
OLD_ARCHIVE_KINDS = "('dataset', 'label_run', 'generation_run', 'approval', 'rule_set')"
NEW_ARCHIVE_KINDS = (
    "('dataset', 'label_run', 'generation_run', 'approval', 'rule_set', 'code_rule')"
)

HEAD_TRIGGER = """
CREATE FUNCTION dw_config_versions_code_rule_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.kind = 'code_rule' AND NOT EXISTS (SELECT 1 FROM dw_code_rules h WHERE h.name = NEW.name)
  THEN
    RAISE EXCEPTION 'code rule % has no head row in dw_code_rules (feature 018)', NEW.name;
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        HEADS,
        sa.Column("name", sa.Text(), primary_key=True),
        sa.Column("behaviour_key", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by", sa.Text()),
        sa.Column("archived_by_origin", sa.String(16)),
        sa.Column("archive_reason", sa.Text()),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_by_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"name ~ '{NAME}'", name=op.f(f"ck_{HEADS}_name_pattern")),
        sa.CheckConstraint(
            f"behaviour_key ~ '{SLUG}'", name=op.f(f"ck_{HEADS}_behaviour_key_slug")
        ),
        sa.CheckConstraint(ARCHIVED_CONSISTENT, name=op.f(f"ck_{HEADS}_archived_consistent")),
        sa.CheckConstraint(
            "created_by_origin IN ('operator', 'agent')", name=op.f(f"ck_{HEADS}_origin_valid")
        ),
        sa.CheckConstraint("length(created_by) > 0", name=op.f(f"ck_{HEADS}_created_by_present")),
        sa.PrimaryKeyConstraint("name", name=op.f(f"pk_{HEADS}")),
    )
    op.create_index("ix_dw_code_rules_behaviour_key", HEADS, ["behaviour_key"])

    op.drop_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, type_="check")
    op.create_check_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, NEW_CONFIG_KINDS)
    op.create_check_constraint(
        op.f(f"ck_{CONFIG}_code_rule_canonical_present"), CONFIG, CODE_RULE_CANONICAL
    )
    op.execute(HEAD_TRIGGER)
    op.execute(
        f"CREATE TRIGGER {CONFIG}_code_rule_head BEFORE INSERT ON {CONFIG} "
        "FOR EACH ROW EXECUTE FUNCTION dw_config_versions_code_rule_head()"
    )

    op.create_table(
        APPROVALS,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("code_sha256", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("changed_by", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("approval_id", sa.String(40)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"code_sha256 {SHA}", name=op.f(f"ck_{APPROVALS}_sha256_pattern")),
        sa.CheckConstraint(
            "action IN ('approve', 'revoke')", name=op.f(f"ck_{APPROVALS}_action_valid")
        ),
        sa.CheckConstraint(
            "origin = 'operator'", name=op.f(f"ck_{APPROVALS}_origin_operator_only")
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f(f"ck_{APPROVALS}_reason_present")
        ),
        sa.CheckConstraint(
            "length(btrim(changed_by)) > 0", name=op.f(f"ck_{APPROVALS}_changed_by_present")
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["dw_approvals.id"],
            name=op.f(f"fk_{APPROVALS}_approval_id_dw_approvals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{APPROVALS}")),
    )
    op.create_index("ix_dw_code_rule_approvals_sha_id", APPROVALS, ["code_sha256", "id"])

    op.create_table(
        RUNS,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("code_sha256", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.Text(), nullable=False),
        sa.Column("bundle_digest", sa.Text(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("failing_case", sa.Text()),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"code_sha256 {SHA}", name=op.f(f"ck_{RUNS}_sha256_pattern")),
        sa.CheckConstraint(
            "length(contract_version) > 0", name=op.f(f"ck_{RUNS}_contract_version_present")
        ),
        sa.CheckConstraint(
            "passed OR failing_case IS NOT NULL OR reason IS NOT NULL",
            name=op.f(f"ck_{RUNS}_failure_named"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{RUNS}")),
    )
    op.create_index(
        "ix_dw_code_rule_contract_runs_sha_contract",
        RUNS,
        ["code_sha256", "contract_version", "id"],
    )
    for table in (APPROVALS, RUNS):
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_insert_only()"
        )

    op.drop_constraint(
        op.f("ck_dw_archive_events_entity_kind_valid"), "dw_archive_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_dw_archive_events_entity_kind_valid"),
        "dw_archive_events",
        f"entity_kind IN {NEW_ARCHIVE_KINDS}",
    )


def downgrade() -> None:
    bind = op.get_bind()
    approvals = bind.execute(
        sa.text(f"SELECT count(*) FROM {APPROVALS}")
    ).scalar_one()  # noqa: S608
    if approvals:
        raise RuntimeError(
            f"refusing to downgrade 0043: {APPROVALS} holds {approvals} operator decision(s) on "
            "code-rule hashes; they are the record of what was approved"
        )
    events = bind.execute(
        sa.text("SELECT count(*) FROM dw_archive_events WHERE entity_kind = 'code_rule'")
    ).scalar_one()
    if events:
        raise RuntimeError(
            f"refusing to downgrade 0043: {events} archive record(s) name a code rule, and the "
            "restored CHECK would not admit them"
        )
    op.drop_constraint(
        op.f("ck_dw_archive_events_entity_kind_valid"), "dw_archive_events", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_dw_archive_events_entity_kind_valid"),
        "dw_archive_events",
        f"entity_kind IN {OLD_ARCHIVE_KINDS}",
    )
    for table in (RUNS, APPROVALS):
        op.execute(f"DROP TRIGGER {table}_insert_only ON {table}")
    op.drop_index("ix_dw_code_rule_contract_runs_sha_contract", table_name=RUNS)
    op.drop_table(RUNS)
    op.drop_index("ix_dw_code_rule_approvals_sha_id", table_name=APPROVALS)
    op.drop_table(APPROVALS)

    op.execute(f"DROP TRIGGER {CONFIG}_code_rule_head ON {CONFIG}")
    op.execute("DROP FUNCTION dw_config_versions_code_rule_head()")
    # Code-rule versions go with the kind: the restored CHECK admits only the 016 kinds.
    op.execute("SET LOCAL dw.purge = 'on'")
    op.execute(f"DELETE FROM {CONFIG} WHERE kind = 'code_rule'")
    op.execute("SET LOCAL dw.purge = 'off'")
    op.drop_constraint(op.f(f"ck_{CONFIG}_code_rule_canonical_present"), CONFIG, type_="check")
    op.drop_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, type_="check")
    op.create_check_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, OLD_CONFIG_KINDS)

    op.drop_index("ix_dw_code_rules_behaviour_key", table_name=HEADS)
    op.drop_table(HEADS)
