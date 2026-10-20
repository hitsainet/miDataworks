"""Feature 002 guards: version immutability, insert-only recipes, manifest and body hashes.

002 FTASKS 3.4, 3.7; FTDD 002 sections 4.2, 4.9. "The database refuses what the service refuses."

The version guard compares ``to_jsonb(NEW)`` with ``to_jsonb(OLD)`` minus the tombstone columns,
so EVERY column is guarded by default, including one added later (FTID IQ3). This replaces the
FTID's ORM-generated column list: it gives the same guarantee without a migration importing the
ORM (a migration that imports models changes meaning when the models change).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""


from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TOMBSTONE = "ARRAY['state', 'deleted_by', 'deleted_by_origin', 'deleted_at', 'delete_reason']"

VERSIONS_IMMUTABLE = f"""
CREATE FUNCTION dw_versions_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  permitted text[] := {TOMBSTONE};
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'dw_versions rows are never deleted; delete is a tombstone (FR-002.37)';
  END IF;
  IF OLD.state = 'completed' AND NEW.state = 'deleted'
     AND (to_jsonb(NEW) - permitted) = (to_jsonb(OLD) - permitted) THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'dw_versions is immutable (FR-002.3): only the tombstone may change a row';
END;
$$;
"""

MANIFEST_HASH = """
CREATE FUNCTION dw_versions_manifest_hash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF encode(sha256(NEW.manifest), 'hex') <> NEW.manifest_sha256 THEN
    RAISE EXCEPTION 'manifest_sha256 does not describe the stored manifest bytes';
  END IF;
  RETURN NEW;
END;
$$;
"""

BODY_HASH = """
CREATE FUNCTION dw_recipe_bodies_hash() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF encode(sha256(NEW.canonical), 'hex') <> NEW.hash THEN
    RAISE EXCEPTION 'recipe body hash does not describe its canonical bytes';
  END IF;
  RETURN NEW;
END;
$$;
"""

TARGET_TYPE = """
CREATE FUNCTION dw_datasets_target_type() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.target_type IS DISTINCT FROM OLD.target_type
     AND EXISTS (SELECT 1 FROM dw_versions WHERE dataset_id = OLD.id) THEN
    RAISE EXCEPTION 'dataset_has_versions: the target type is fixed once a version exists';
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    for sql in (VERSIONS_IMMUTABLE, MANIFEST_HASH, BODY_HASH, TARGET_TYPE):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER dw_versions_immutable BEFORE UPDATE OR DELETE ON dw_versions "
        "FOR EACH ROW EXECUTE FUNCTION dw_versions_immutable()"
    )
    op.execute(
        "CREATE TRIGGER dw_versions_manifest_hash BEFORE INSERT ON dw_versions "
        "FOR EACH ROW EXECUTE FUNCTION dw_versions_manifest_hash()"
    )
    op.execute(
        "CREATE TRIGGER dw_recipe_bodies_hash BEFORE INSERT ON dw_recipe_bodies "
        "FOR EACH ROW EXECUTE FUNCTION dw_recipe_bodies_hash()"
    )
    for table in ("dw_recipe_bodies", "dw_recipe_revisions"):
        op.execute(
            f"CREATE TRIGGER {table}_insert_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION dw_append_only()"
        )
    op.execute(
        "CREATE TRIGGER dw_datasets_target_type BEFORE UPDATE ON dw_datasets "
        "FOR EACH ROW EXECUTE FUNCTION dw_datasets_target_type()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER dw_datasets_target_type ON dw_datasets")
    for table in ("dw_recipe_bodies", "dw_recipe_revisions"):
        op.execute(f"DROP TRIGGER {table}_insert_only ON {table}")
    op.execute("DROP TRIGGER dw_recipe_bodies_hash ON dw_recipe_bodies")
    op.execute("DROP TRIGGER dw_versions_manifest_hash ON dw_versions")
    op.execute("DROP TRIGGER dw_versions_immutable ON dw_versions")
    for fn in (
        "dw_datasets_target_type",
        "dw_recipe_bodies_hash",
        "dw_versions_manifest_hash",
        "dw_versions_immutable",
    ):
        op.execute(f"DROP FUNCTION {fn}()")
