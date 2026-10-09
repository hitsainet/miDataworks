"""Database guards for feature 002 (tasks 3.3, 3.5, 3.6, 3.7; FR-002.3, FR-002.13, FR-002.24).

"The database refuses what the service refuses": these tests go straight to SQL, around every
service, because the guard is what stops a future code path that forgets the rule.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.database import get_sync_engine, sync_session_factory
from src.models import Version
from src.models.row_event import PARTITIONS
from src.models.version import TOMBSTONE_COLUMNS
from tests.support import db_factories as f


@pytest.fixture
def db(clean_db: None) -> Iterator[Session]:
    session = sync_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _refused(db: Session, sql: str, params: dict[str, object], because: str = "") -> bool:
    """True when the database refuses the statement — for the stated reason, when one is given,
    so a foreign-key or CHECK refusal cannot stand in for the guard under test."""
    try:
        with db.begin_nested():
            db.execute(text(sql), params)
    except DBAPIError as exc:
        return because in str(exc.orig)
    return False


#: A value of the right type for each guarded column, different from the factory's.
def _new_value(column: str, current: object) -> object:
    if isinstance(current, bool):
        return not current
    if isinstance(current, int):
        return current + 1
    if isinstance(current, bytes):
        return current + b"x"
    if column.endswith("_id") or column == "id":
        return None if current is not None else str(uuid.uuid4())
    return "changed"


GUARDED = [c.name for c in Version.__table__.columns if c.name not in TOMBSTONE_COLUMNS]


@pytest.mark.parametrize("column", GUARDED)
def test_every_identity_column_update_is_rejected(db: Session, column: str) -> None:
    v = f.version(db)
    db.commit()
    current = getattr(v, column)
    if column in {"inputs", "bindings", "splits", "warnings", "drop_summary", "column_roles"}:
        sql = f"UPDATE dw_versions SET {column} = '[\"x\"]'::jsonb WHERE id = :id"
        params: dict[str, object] = {"id": v.id}
    elif column in {"created_at"}:
        sql = f"UPDATE dw_versions SET {column} = now() - interval '1 day' WHERE id = :id"
        params = {"id": v.id}
    else:
        sql = f"UPDATE dw_versions SET {column} = :value WHERE id = :id"
        params = {"id": v.id, "value": _new_value(column, current)}
    assert _refused(db, sql, params, because="immutable"), f"dw_versions.{column} was not guarded"


def test_the_tombstone_is_the_one_permitted_update(db: Session) -> None:
    v = f.version(db)
    db.commit()
    db.execute(
        text(
            "UPDATE dw_versions SET state = 'deleted', deleted_by = 'Ada', "
            "deleted_by_origin = 'operator', deleted_at = now(), delete_reason = 'r' "
            "WHERE id = :id"
        ),
        {"id": v.id},
    )
    db.commit()
    assert (
        db.execute(text("SELECT state FROM dw_versions WHERE id = :id"), {"id": v.id}).scalar_one()
        == "deleted"
    )


def test_a_tombstone_that_also_edits_identity_is_rejected(db: Session) -> None:
    v = f.version(db)
    db.commit()
    assert _refused(
        db,
        "UPDATE dw_versions SET state = 'deleted', deleted_by = 'Ada', deleted_by_origin = "
        "'operator', deleted_at = now(), seed = seed + 1 WHERE id = :id",
        {"id": v.id},
    )


def test_a_deleted_version_cannot_be_revived(db: Session) -> None:
    v = f.version(db)
    db.commit()
    db.execute(
        text(
            "UPDATE dw_versions SET state = 'deleted', deleted_by = 'Ada', "
            "deleted_by_origin = 'operator', deleted_at = now() WHERE id = :id"
        ),
        {"id": v.id},
    )
    db.commit()
    assert _refused(
        db,
        "UPDATE dw_versions SET state = 'completed', deleted_by = NULL, deleted_by_origin = NULL,"
        " deleted_at = NULL WHERE id = :id",
        {"id": v.id},
    )


def test_a_physical_delete_is_rejected(db: Session) -> None:
    v = f.version(db)
    db.commit()
    assert _refused(db, "DELETE FROM dw_versions WHERE id = :id", {"id": v.id})


def test_a_wrong_manifest_hash_is_rejected_on_insert(db: Session) -> None:
    with pytest.raises(DBAPIError):
        with db.begin_nested():
            f.version(db, manifest_sha256=hashlib.sha256(b"something else").hexdigest())


def test_a_column_added_later_is_guarded_by_default(db: Session) -> None:
    """IQ3: a throwaway column, added in this test database only, is immutable with no edit to
    the trigger. Dropped again at the end so the shared schema is unchanged."""
    v = f.version(db)
    db.commit()
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_versions ADD COLUMN zz_probe text DEFAULT 'a'"))
    try:
        assert _refused(
            db, "UPDATE dw_versions SET zz_probe = 'b' WHERE id = :id", {"id": v.id}, "immutable"
        )
    finally:
        db.rollback()
        with get_sync_engine().begin() as conn:
            conn.execute(text("ALTER TABLE dw_versions DROP COLUMN zz_probe"))


class TestRecipes:
    def test_a_recipe_body_update_is_rejected(self, db: Session) -> None:
        _, rev = f.recipe(db)
        db.commit()
        assert _refused(
            db,
            "UPDATE dw_recipe_bodies SET body = '{}'::jsonb WHERE hash = :h",
            {"h": rev.recipe_hash},
        )

    def test_a_recipe_body_delete_is_rejected(self, db: Session) -> None:
        _, rev = f.recipe(db)
        db.commit()
        assert _refused(db, "DELETE FROM dw_recipe_bodies WHERE hash = :h", {"h": rev.recipe_hash})

    def test_a_body_whose_hash_is_not_its_bytes_is_rejected(self, db: Session) -> None:
        assert _refused(
            db,
            "INSERT INTO dw_recipe_bodies (hash, canonical, body) VALUES (:h, :c, '{}'::jsonb)",
            {"h": "0" * 64, "c": b"{}"},
        )

    def test_a_revision_cannot_be_updated_or_deleted(self, db: Session) -> None:
        _, rev = f.recipe(db)
        db.commit()
        assert _refused(
            db, "UPDATE dw_recipe_revisions SET imported = true WHERE id = :id", {"id": rev.id}
        )
        assert _refused(db, "DELETE FROM dw_recipe_revisions WHERE id = :id", {"id": rev.id})


class TestDatasetTargetType:
    def test_target_type_may_change_while_no_version_exists(self, db: Session) -> None:
        ds = f.dataset(db, target_type="untyped")
        db.commit()
        db.execute(text("UPDATE dw_datasets SET target_type = 'sft' WHERE id = :id"), {"id": ds.id})
        db.commit()

    def test_target_type_is_fixed_once_a_version_exists(self, db: Session) -> None:
        ds = f.dataset(db, target_type="detector")
        f.version(db, ds)
        db.commit()
        assert _refused(
            db, "UPDATE dw_datasets SET target_type = 'sft' WHERE id = :id", {"id": ds.id}
        )

    def test_other_dataset_columns_still_change_with_versions(self, db: Session) -> None:
        ds = f.dataset(db)
        f.version(db, ds)
        db.commit()
        db.execute(text("UPDATE dw_datasets SET description = 'x' WHERE id = :id"), {"id": ds.id})
        db.commit()


class TestRowEventPartitions:
    def test_all_32_partitions_exist(self, db: Session) -> None:
        names = set(
            db.execute(
                text(
                    "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                    "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'dw_row_events'"
                )
            ).scalars()
        )
        assert names == {f"dw_row_events_p{n:02d}" for n in range(PARTITIONS)}

    def test_the_parent_is_hash_partitioned_on_step_execution(self, db: Session) -> None:
        key = db.execute(text("SELECT pg_get_partkeydef('dw_row_events'::regclass)")).scalar_one()
        assert key == "HASH (step_execution_id)"

    def test_an_insert_lands_in_a_partition_and_both_history_indexes_are_used(
        self, db: Session
    ) -> None:
        execution = str(uuid.uuid4())
        key = hashlib.sha256(b"k").digest()
        db.execute(
            text(
                "INSERT INTO dw_row_events (step_execution_id, seq, kind, row_key, occurrence, "
                "new_row_key, reason_code, reason, statistic_name, statistic_value) VALUES "
                "(:e, 0, 'changed', :k, 0, :n, 'upper', 'upper-cased', 'len', 3)"
            ),
            {"e": execution, "k": key, "n": hashlib.sha256(b"n").digest()},
        )
        db.commit()
        part = db.execute(
            text("SELECT tableoid::regclass::text FROM dw_row_events WHERE step_execution_id = :e"),
            {"e": execution},
        ).scalar_one()
        assert part.startswith("dw_row_events_p")
        db.execute(text("SET enable_seqscan = off"))
        plan_old = "\n".join(
            db.execute(
                text("EXPLAIN SELECT * FROM dw_row_events WHERE row_key = :k"), {"k": key}
            ).scalars()
        )
        plan_new = "\n".join(
            db.execute(
                text("EXPLAIN SELECT * FROM dw_row_events WHERE new_row_key = :k"), {"k": key}
            ).scalars()
        )
        assert "row_key_idx" in plan_old, plan_old
        assert "new_row_key_idx" in plan_new, plan_new

    def test_events_are_append_only(self, db: Session) -> None:
        execution = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO dw_row_events (step_execution_id, seq, kind, row_key, occurrence, "
                "reason_code, reason) VALUES (:e, 0, 'added', :k, 0, 'gen', 'generated')"
            ),
            {"e": execution, "k": hashlib.sha256(b"k").digest()},
        )
        db.commit()
        assert _refused(
            db,
            "UPDATE dw_row_events SET reason = 'x' WHERE step_execution_id = :e",
            {"e": execution},
        )
        assert _refused(
            db, "DELETE FROM dw_row_events WHERE step_execution_id = :e", {"e": execution}
        )

    def test_a_drop_without_a_statistic_is_refused(self, db: Session) -> None:
        assert _refused(
            db,
            "INSERT INTO dw_row_events (step_execution_id, seq, kind, row_key, occurrence, "
            "reason_code, reason) VALUES (:e, 0, 'dropped', :k, 0, 'short', 'too short')",
            {"e": str(uuid.uuid4()), "k": hashlib.sha256(b"k").digest()},
        )


def test_version_inputs_restrict_a_source_delete(db: Session) -> None:
    src = f.source(db)
    v = f.version(db)
    db.execute(
        text(
            "INSERT INTO dw_version_inputs (version_id, position, kind, source_id) "
            "VALUES (:v, 0, 'source', :s)"
        ),
        {"v": v.id, "s": src.id},
    )
    db.commit()
    fks = inspect(get_sync_engine()).get_foreign_keys("dw_version_inputs")
    source_fk = next(fk for fk in fks if fk["referred_table"] == "dw_sources")
    assert source_fk["options"].get("ondelete") == "RESTRICT"
