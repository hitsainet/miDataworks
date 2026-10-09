"""Feature 001's database guards (001 FTASKS 2.6, 2.7, 2.9; FR-001.4, FR-001.5, FR-001.38).

Straight SQL, around every service: the guard is what stops a future code path that forgets.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from src.core.database import get_sync_engine, sync_session_factory
from tests.support import db_factories as f

COMMIT = "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac"
OTHER = "1" * 40


@pytest.fixture
def db(clean_db: None) -> Iterator[Session]:
    session = sync_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _refused(db: Session, sql: str, params: dict[str, object], because: str = "") -> bool:
    try:
        with db.begin_nested():
            db.execute(text(sql), params)
    except DBAPIError as exc:
        return because in str(exc.orig)
    return False


class TestReadySourceIsFrozen:
    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("resolved_commit", OTHER),
            ("licence_raw", '"mit"'),
            ("licence_display", "mit"),
            ("detection", '{"x": 1}'),
            ("repo_id", "other/repo"),
            ("display_name", "renamed"),
        ],
    )
    def test_identity_licence_and_detection_columns_are_frozen(
        self, db: Session, column: str, value: str
    ) -> None:
        src = f.source(db)
        db.commit()
        expr = "CAST(:v AS jsonb)" if column in {"licence_raw", "detection"} else ":v"
        assert _refused(
            db,
            f"UPDATE dw_sources SET {column} = {expr} WHERE id = :id",
            {"v": value, "id": src.id},
            because="frozen",
        )

    def test_ready_back_to_importing_is_refused(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        assert _refused(
            db, "UPDATE dw_sources SET state = 'importing' WHERE id = :id", {"id": src.id}, "frozen"
        )

    def test_ready_to_deleted_with_the_deleted_columns_passes(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        db.execute(
            text(
                "UPDATE dw_sources SET state = 'deleted', deleted_by = 'Ada', "
                "deleted_by_origin = 'operator', deleted_at = now() WHERE id = :id"
            ),
            {"id": src.id},
        )
        db.commit()

    def test_a_deleted_source_stays_deleted(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        db.execute(
            text("UPDATE dw_sources SET state = 'deleted', deleted_at = now() WHERE id = :id"),
            {"id": src.id},
        )
        db.commit()
        assert _refused(
            db, "UPDATE dw_sources SET state = 'ready' WHERE id = :id", {"id": src.id}, "frozen"
        )

    def test_importing_may_become_ready_with_its_detection(self, db: Session) -> None:
        src = f.source(db, state="importing")
        db.commit()
        db.execute(
            text(
                "UPDATE dw_sources SET state = 'ready', detection = '{\"a\": 1}'::jsonb, "
                "ready_at = now() WHERE id = :id"
            ),
            {"id": src.id},
        )
        db.commit()

    def test_a_physical_delete_is_refused(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        assert _refused(
            db, "DELETE FROM dw_sources WHERE id = :id", {"id": src.id}, "never deleted"
        )

    def test_a_column_added_later_is_guarded_by_default(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        with get_sync_engine().begin() as conn:
            conn.execute(text("ALTER TABLE dw_sources ADD COLUMN zz_probe text DEFAULT 'a'"))
        try:
            assert _refused(
                db, "UPDATE dw_sources SET zz_probe = 'b' WHERE id = :id", {"id": src.id}, "frozen"
            )
        finally:
            db.rollback()
            with get_sync_engine().begin() as conn:
                conn.execute(text("ALTER TABLE dw_sources DROP COLUMN zz_probe"))


class TestAppendOnly:
    def test_files_refuse_update_and_delete(self, db: Session) -> None:
        src = f.source(db)
        row = f.source_file(db, src, "train", "sources/x/train.parquet", "a" * 64, 3, 10)
        db.commit()
        assert _refused(
            db, "UPDATE dw_source_files SET rows = 4 WHERE id = :id", {"id": row.id}, "append-only"
        )
        assert _refused(
            db, "DELETE FROM dw_source_files WHERE id = :id", {"id": row.id}, "append-only"
        )

    def test_a_reserved_column_in_a_file_schema_is_refused(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        assert _refused(
            db,
            "INSERT INTO dw_source_files (id, source_id, split, path, rows, bytes, sha256, columns) "
            "VALUES (:id, :s, 'train', 'p', 1, 1, :h, '[{\"name\": \"_dw_row_key\"}]'::jsonb)",
            {"id": str(uuid.uuid4()), "s": src.id, "h": "a" * 64},
            "no_reserved_columns",
        )

    def test_annotations_refuse_update_and_delete(self, db: Session) -> None:
        src = f.source(db)
        db.commit()
        annotation = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO dw_source_annotations (id, source_id, kind, redistribution, value, "
                "reason, created_by, created_by_origin) VALUES (:id, :s, 'licence', 'permits', "
                "'{}'::jsonb, 'checked the card', 'Ada', 'operator')"
            ),
            {"id": annotation, "s": src.id},
        )
        db.commit()
        assert _refused(
            db,
            "UPDATE dw_source_annotations SET reason = 'x' WHERE id = :id",
            {"id": annotation},
            "append-only",
        )
        assert _refused(
            db,
            "DELETE FROM dw_source_annotations WHERE id = :id",
            {"id": annotation},
            "append-only",
        )


class TestIdentityUniqueness:
    def test_two_live_rows_with_one_hf_identity_cannot_coexist(self, db: Session) -> None:
        f.source(db, state="importing")
        db.commit()
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                f.source(db, state="ready")

    def test_a_failed_row_does_not_block_a_new_import(self, db: Session) -> None:
        f.source(db, state="importing")
        db.execute(text("UPDATE dw_sources SET state = 'failed'"))
        db.commit()
        f.source(db, state="importing")
        db.commit()

    def test_another_commit_or_config_is_another_identity(self, db: Session) -> None:
        f.source(db)
        f.source(db, commit=OTHER)
        db.commit()

    def test_upload_content_hash_is_unique_while_live(self, db: Session) -> None:
        f.source(db, kind="upload", content_hash="c" * 64)
        db.commit()
        with pytest.raises(DBAPIError):
            with db.begin_nested():
                f.source(db, kind="upload", content_hash="c" * 64)


class TestAgentAnnotationsCarryApproval:
    """S3-01 / FR-001.38 at the database: the service is not the only line of defence."""

    def _insert(
        self, db: Session, origin: str, approval: str | None, approved_by: str | None
    ) -> bool:
        src = f.source(db)
        db.commit()
        return _refused(
            db,
            "INSERT INTO dw_source_annotations (id, source_id, kind, redistribution, value, "
            "reason, created_by, created_by_origin, approval_id, approved_by) VALUES (:id, :s, "
            "'licence', 'permits', '{}'::jsonb, 'r', :who, :o, :a, :b)",
            {
                "id": str(uuid.uuid4()),
                "s": src.id,
                "who": "agent:x" if origin == "agent" else "Ada",
                "o": origin,
                "a": approval,
                "b": approved_by,
            },
            "agent_needs_approval",
        )

    def test_an_agent_row_without_an_approval_is_refused(self, db: Session) -> None:
        assert self._insert(db, "agent", None, None)

    def test_an_operator_row_with_an_approval_is_refused(self, db: Session) -> None:
        db.execute(
            text(
                "INSERT INTO dw_approvals (id, action, target, summary, payload, request_digest, "
                "requested_by, status, expires_at) VALUES ('apr_1', 'source_annotate', 't', 's', "
                "'{}'::jsonb, 'd', 'agent:x', 'executed', now())"
            )
        )
        db.commit()
        assert self._insert(db, "operator", "apr_1", "Ada")
