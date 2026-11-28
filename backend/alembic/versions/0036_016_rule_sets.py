"""Feature 016 slice 2: rule sets as a kind of the versioned configuration store (FTID 016 section 4.1).

1. ``dw_rule_sets`` (new): the head row of a rule-set name - its one behaviour key and the four
   archive columns. Not insert-only: archiving is an UPDATE of this row.
2. ``dw_config_versions``: kind ``rule_set`` admitted; ``body_canonical`` holds the exact RFC 8785
   bytes the identity hashes (D38-4), and the database itself proves ``body_sha256`` is their
   sha256; a ``rule_set`` row needs its head (trigger ``dw_config_versions_rule_set_head``). The
   insert-only trigger is split as ``0032`` split others: UPDATE always refused, DELETE refused
   unless ``dw.purge`` is on (the purge of an archived, unused rule set).
3. ``dw_rule_set_uses`` (new, insert-only): what each step execution ran - one row per
   (step execution, rule-set version) with the rule set's own behaviour key and the step's ``use``.
   RESTRICT foreign keys: a used version is never deleted.
4. ``dw_archive_events`` and ``dw_purge_events`` admit the entity kind ``rule_set``.

``downgrade`` refuses while ``dw_rule_set_uses`` holds rows (they are build records), then drops
everything and restores the original CHECKs and the unsplit trigger. Literals here are written out
(a migration never imports app code); ``test_rule_set_models.py`` pins them to the models.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEADS = "dw_rule_sets"
USES = "dw_rule_set_uses"
CONFIG = "dw_config_versions"
NAME = "^[a-z0-9][a-z0-9-]{0,63}$"
SLUG = "^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$"
FLAG_OFF = "current_setting('dw.purge', true) IS DISTINCT FROM 'on'"
ARCHIVED_CONSISTENT = (
    "(archived_at IS NULL AND archived_by IS NULL AND archived_by_origin IS NULL "
    "AND archive_reason IS NULL) OR "
    "(archived_at IS NOT NULL AND archived_by IS NOT NULL AND length(archived_by) > 0 "
    "AND archived_by_origin IN ('operator', 'agent'))"
)
OLD_KINDS = "('dataset', 'label_run', 'generation_run', 'approval')"
NEW_KINDS = "('dataset', 'label_run', 'generation_run', 'approval', 'rule_set')"

HEAD_TRIGGER = """
CREATE FUNCTION dw_config_versions_rule_set_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.kind = 'rule_set' AND NOT EXISTS (SELECT 1 FROM dw_rule_sets h WHERE h.name = NEW.name)
  THEN
    RAISE EXCEPTION 'rule set % has no head row in dw_rule_sets (feature 016)', NEW.name;
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
    op.create_index("ix_dw_rule_sets_behaviour_key", HEADS, ["behaviour_key"])

    op.drop_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, "kind IN ('selector', 'grader', 'rule_set')"
    )
    op.add_column(CONFIG, sa.Column("body_canonical", sa.Text()))
    op.create_check_constraint(
        op.f(f"ck_{CONFIG}_rule_set_canonical_present"),
        CONFIG,
        "kind <> 'rule_set' OR body_canonical IS NOT NULL",
    )
    op.create_check_constraint(
        op.f(f"ck_{CONFIG}_canonical_sha"),
        CONFIG,
        "body_canonical IS NULL OR "
        "encode(sha256(convert_to(body_canonical, 'UTF8')), 'hex') = body_sha256",
    )
    op.execute(HEAD_TRIGGER)
    op.execute(
        f"CREATE TRIGGER {CONFIG}_rule_set_head BEFORE INSERT ON {CONFIG} "
        "FOR EACH ROW EXECUTE FUNCTION dw_config_versions_rule_set_head()"
    )
    op.execute(f"DROP TRIGGER {CONFIG}_insert_only ON {CONFIG}")
    op.execute(
        f"CREATE TRIGGER {CONFIG}_insert_only BEFORE UPDATE ON {CONFIG} "
        "FOR EACH ROW EXECUTE FUNCTION dw_insert_only()"
    )
    op.execute(
        f"CREATE TRIGGER {CONFIG}_insert_only_delete BEFORE DELETE ON {CONFIG} "
        f"FOR EACH ROW WHEN ({FLAG_OFF}) EXECUTE FUNCTION dw_insert_only()"
    )

    op.create_table(
        USES,
        sa.Column("step_execution_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("config_version_id", sa.String(40), nullable=False),
        sa.Column("rule_set_sha256", sa.String(64), nullable=False),
        sa.Column("behaviour_key", sa.Text(), nullable=False),
        sa.Column("use", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "rule_set_sha256 ~ '^[0-9a-f]{64}$'", name=op.f(f"ck_{USES}_sha256_pattern")
        ),
        sa.CheckConstraint(f"behaviour_key ~ '{SLUG}'", name=op.f(f"ck_{USES}_behaviour_key_slug")),
        sa.CheckConstraint(
            "use IN ('label', 'veto', 'screen', 'decoy_check', 'conflict_scan')",
            name=op.f(f"ck_{USES}_use_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["step_execution_id"],
            ["dw_step_executions.id"],
            name=op.f(f"fk_{USES}_step_execution_id_dw_step_executions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["config_version_id"],
            [f"{CONFIG}.id"],
            name=op.f(f"fk_{USES}_config_version_id_{CONFIG}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("step_execution_id", "config_version_id", name=op.f(f"pk_{USES}")),
    )
    op.create_index("ix_dw_rule_set_uses_config_version", USES, ["config_version_id"])
    op.execute(
        f"CREATE TRIGGER {USES}_insert_only BEFORE UPDATE ON {USES} "
        "FOR EACH ROW EXECUTE FUNCTION dw_insert_only()"
    )
    op.execute(
        f"CREATE TRIGGER {USES}_insert_only_delete BEFORE DELETE ON {USES} "
        f"FOR EACH ROW WHEN ({FLAG_OFF}) EXECUTE FUNCTION dw_insert_only()"
    )

    for table in ("dw_archive_events", "dw_purge_events"):
        op.drop_constraint(op.f(f"ck_{table}_entity_kind_valid"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_entity_kind_valid"), table, f"entity_kind IN {NEW_KINDS}"
        )


def downgrade() -> None:
    bind = op.get_bind()
    used = bind.execute(sa.text(f"SELECT count(*) FROM {USES}")).scalar_one()  # noqa: S608
    if used:
        raise RuntimeError(
            f"refusing to downgrade 0036: {USES} holds {used} build record(s) of rule sets a "
            "version executed; purge those versions first"
        )
    rule_set_events = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM dw_archive_events WHERE entity_kind = 'rule_set') + "
            "(SELECT count(*) FROM dw_purge_events WHERE entity_kind = 'rule_set')"
        )
    ).scalar_one()
    if rule_set_events:
        raise RuntimeError(
            f"refusing to downgrade 0036: {rule_set_events} archive or purge record(s) name a "
            "rule set, and the restored CHECKs would not admit them"
        )
    for table in ("dw_archive_events", "dw_purge_events"):
        op.drop_constraint(op.f(f"ck_{table}_entity_kind_valid"), table, type_="check")
        op.create_check_constraint(
            op.f(f"ck_{table}_entity_kind_valid"), table, f"entity_kind IN {OLD_KINDS}"
        )
    op.execute(f"DROP TRIGGER {USES}_insert_only_delete ON {USES}")
    op.execute(f"DROP TRIGGER {USES}_insert_only ON {USES}")
    op.drop_index("ix_dw_rule_set_uses_config_version", table_name=USES)
    op.drop_table(USES)

    op.execute(f"DROP TRIGGER {CONFIG}_insert_only_delete ON {CONFIG}")
    op.execute(f"DROP TRIGGER {CONFIG}_insert_only ON {CONFIG}")
    op.execute(f"DROP TRIGGER {CONFIG}_rule_set_head ON {CONFIG}")
    op.execute("DROP FUNCTION dw_config_versions_rule_set_head()")
    # Rule-set versions go with the kind: the restored CHECK admits only selector and grader.
    op.execute("SET LOCAL dw.purge = 'on'")
    op.execute(f"DELETE FROM {CONFIG} WHERE kind = 'rule_set'")
    op.execute("SET LOCAL dw.purge = 'off'")
    op.execute(
        f"CREATE TRIGGER {CONFIG}_insert_only BEFORE UPDATE OR DELETE ON {CONFIG} "
        "FOR EACH ROW EXECUTE FUNCTION dw_insert_only()"
    )
    op.drop_constraint(op.f(f"ck_{CONFIG}_canonical_sha"), CONFIG, type_="check")
    op.drop_constraint(op.f(f"ck_{CONFIG}_rule_set_canonical_present"), CONFIG, type_="check")
    op.drop_column(CONFIG, "body_canonical")
    op.drop_constraint(op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, type_="check")
    op.create_check_constraint(
        op.f(f"ck_{CONFIG}_kind_valid"), CONFIG, "kind IN ('selector', 'grader')"
    )

    op.drop_index("ix_dw_rule_sets_behaviour_key", table_name=HEADS)
    op.drop_table(HEADS)
