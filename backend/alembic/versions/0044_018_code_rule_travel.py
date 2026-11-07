"""Feature 018 slice 4: code rules in builds, travel and purge (FTID 018 §4.2; FTDD 018 §5.6, §5.7).

1. ``dw_code_rule_uses`` (new, insert-only): one row per code rule a step execution ran - the rule
   set that called it, the exact code-rule version, the runner contract and bundle the runner
   reported, the approve row in force when the step was resolved, and the rows that failed. UPDATE is
   always refused; DELETE only under ``dw.purge`` (the ``0036`` form), so a purge can remove the
   build records of what it removes.
2. ``dw_step_executions.reuse_blocked`` (decision I7, ENH-006 D36): a completed step whose code
   rules met a host fault (the runner down, a starved lane) is never reused; the live-identity
   unique index is rebuilt to leave such a row out, so the next build computes the step afresh.
3. ``dw_publish_files.role`` admits ``code_rule``; ``dw_publish_builds.code_rule_files`` (NULL =
   built before 018) records the two files per carried code rule a publish build wrote.
4. ``dw_versions.code_rules_recorded`` (false for every version that existed before, true for every
   later one), so a pre-018 version reads "code rules not recorded", never "none".
5. ``dw_purge_events`` admits ``code_rule``: an archived, unused code rule is purgeable now that the
   build records exist.

``downgrade`` refuses while a use row, a ``reuse_blocked`` execution, a published code-rule file or
a code-rule purge record exists. Literals are written out (a migration never imports app code);
``tests/integration/code_rules/test_migration_0044.py`` pins them to the models.

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USES = "dw_code_rule_uses"
STEPS = "dw_step_executions"
FILES = "dw_publish_files"
BUILDS = "dw_publish_builds"
VERSIONS = "dw_versions"
PURGES = "dw_purge_events"
CONFIG = "dw_config_versions"
FLAG_OFF = "current_setting('dw.purge', true) IS DISTINCT FROM 'on'"
SHA = "~ '^[0-9a-f]{64}$'"
LIVE_INDEX = "uq_dw_step_executions_identity_live"
OLD_LIVE = "state IN ('queued', 'running', 'completed')"
NEW_LIVE = "state IN ('queued', 'running', 'completed') AND NOT reuse_blocked"
OLD_ROLES = "role IN ('split', 'card', 'manifest', 'rule_set')"
NEW_ROLES = "role IN ('split', 'card', 'manifest', 'rule_set', 'code_rule')"
OLD_PURGE_KINDS = "('dataset', 'label_run', 'generation_run', 'approval', 'rule_set')"
NEW_PURGE_KINDS = "('dataset', 'label_run', 'generation_run', 'approval', 'rule_set', 'code_rule')"


def upgrade() -> None:
    op.create_table(
        USES,
        sa.Column("step_execution_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("code_sha256", sa.String(64), nullable=False),
        sa.Column("rule_set_config_version_id", sa.String(40), nullable=False),
        sa.Column("code_rule_config_version_id", sa.String(40), nullable=False),
        sa.Column("runner_contract", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.Text()),
        sa.Column("bundle_digest", sa.Text()),
        sa.Column("approval_row_id", sa.BigInteger(), nullable=False),
        sa.Column("failed_rows", sa.Integer(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(f"code_sha256 {SHA}", name=op.f(f"ck_{USES}_sha256_pattern")),
        sa.CheckConstraint("failed_rows >= 0", name=op.f(f"ck_{USES}_failed_rows_nonnegative")),
        sa.CheckConstraint(
            "length(runner_contract) > 0", name=op.f(f"ck_{USES}_runner_contract_present")
        ),
        sa.ForeignKeyConstraint(
            ["step_execution_id"],
            [f"{STEPS}.id"],
            name=op.f(f"fk_{USES}_step_execution_id_{STEPS}"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_config_version_id"],
            [f"{CONFIG}.id"],
            name=op.f(f"fk_{USES}_rule_set_config_version_id_{CONFIG}"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["code_rule_config_version_id"],
            [f"{CONFIG}.id"],
            name=op.f(f"fk_{USES}_code_rule_config_version_id_{CONFIG}"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_row_id"],
            ["dw_code_rule_approvals.id"],
            name=op.f(f"fk_{USES}_approval_row_id_dw_code_rule_approvals"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("step_execution_id", "code_sha256", name=op.f(f"pk_{USES}")),
    )
    op.create_index("ix_dw_code_rule_uses_code_version", USES, ["code_rule_config_version_id"])
    op.create_index("ix_dw_code_rule_uses_sha", USES, ["code_sha256"])
    op.execute(
        f"CREATE TRIGGER {USES}_insert_only BEFORE UPDATE ON {USES} "
        "FOR EACH ROW EXECUTE FUNCTION dw_insert_only()"
    )
    op.execute(
        f"CREATE TRIGGER {USES}_insert_only_delete BEFORE DELETE ON {USES} "
        f"FOR EACH ROW WHEN ({FLAG_OFF}) EXECUTE FUNCTION dw_insert_only()"
    )

    op.add_column(
        STEPS,
        sa.Column("reuse_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.drop_index(LIVE_INDEX, table_name=STEPS)
    op.create_index(
        LIVE_INDEX, STEPS, ["identity_digest"], unique=True, postgresql_where=sa.text(NEW_LIVE)
    )

    op.drop_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, type_="check")
    op.create_check_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, NEW_ROLES)
    op.add_column(BUILDS, sa.Column("code_rule_files", postgresql.JSONB()))
    op.add_column(
        VERSIONS,
        sa.Column(
            "code_rules_recorded", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.alter_column(VERSIONS, "code_rules_recorded", server_default=sa.text("true"))

    op.drop_constraint(op.f(f"ck_{PURGES}_entity_kind_valid"), PURGES, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{PURGES}_entity_kind_valid"), PURGES, f"entity_kind IN {NEW_PURGE_KINDS}"
    )


def _count(sql: str) -> int:
    return int(op.get_bind().execute(sa.text(sql)).scalar_one())


def downgrade() -> None:
    held = {
        f"{USES} holds build record(s) of code rules a step ran": f"SELECT count(*) FROM {USES}",
        f"{STEPS} holds reuse-blocked step execution(s)": (
            f"SELECT count(*) FROM {STEPS} WHERE reuse_blocked"  # noqa: S608
        ),
        f"{FILES} holds published code-rule file(s)": (
            f"SELECT count(*) FROM {FILES} WHERE role = 'code_rule'"  # noqa: S608
        ),
        f"{PURGES} holds code-rule purge record(s)": (
            f"SELECT count(*) FROM {PURGES} WHERE entity_kind = 'code_rule'"  # noqa: S608
        ),
    }
    for what, sql in held.items():
        found = _count(sql)
        if found:
            raise RuntimeError(f"refusing to downgrade 0044: {what} ({found}); they are records")
    op.drop_constraint(op.f(f"ck_{PURGES}_entity_kind_valid"), PURGES, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{PURGES}_entity_kind_valid"), PURGES, f"entity_kind IN {OLD_PURGE_KINDS}"
    )
    op.drop_column(VERSIONS, "code_rules_recorded")
    op.drop_column(BUILDS, "code_rule_files")
    op.drop_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, type_="check")
    op.create_check_constraint(op.f(f"ck_{FILES}_role_valid"), FILES, OLD_ROLES)
    op.drop_index(LIVE_INDEX, table_name=STEPS)
    op.create_index(
        LIVE_INDEX, STEPS, ["identity_digest"], unique=True, postgresql_where=sa.text(OLD_LIVE)
    )
    op.drop_column(STEPS, "reuse_blocked")
    op.execute(f"DROP TRIGGER {USES}_insert_only_delete ON {USES}")
    op.execute(f"DROP TRIGGER {USES}_insert_only ON {USES}")
    op.drop_index("ix_dw_code_rule_uses_sha", table_name=USES)
    op.drop_index("ix_dw_code_rule_uses_code_version", table_name=USES)
    op.drop_table(USES)
