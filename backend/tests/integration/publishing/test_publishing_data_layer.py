"""Feature 008's tables and their guards (008 FTASKS 4.3; EC-7 database half; FR-008.15).

"The database refuses what the service refuses": the publication record, the per-file evidence,
a completed check snapshot, configuration versions and terms notes cannot be rewritten.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models import (
    ConfigVersion,
    ModelTermsNote,
    Publish,
    PublishBuild,
    PublishCheckRun,
    PublishFile,
)
from tests.support import db_factories as f


def _version(db) -> tuple[str, str]:
    version = f.version(db)
    build = PublishBuild(
        id=new_id("pbld"),
        version_id=version.id,
        projection_digest="d" * 64,
        projection={},
        status="completed",
        files=[],
        started_by="Test Operator",
        started_by_origin="operator",
    )
    db.add(build)
    db.flush()
    return version.id, build.id


def _publish(
    db, version_id: str, build_id: str, status: str = "queued", repo: str = "a/b"
) -> Publish:
    job = f.job(db, kind="publish", status="queued")
    row = Publish(
        id=new_id("pub"),
        job_id=job.id,
        version_id=version_id,
        build_id=build_id,
        kind="publish",
        repo_id=repo,
        requested_visibility="private",
        status=status,
        card_prose="",
        request_digest="e" * 64,
        started_by="Test Operator",
        started_by_origin="operator",
    )
    if status == "published":
        row.commit, row.published_manifest, row.visibility_after = "c" * 40, {"x": 1}, "private"
    db.add(row)
    db.flush()
    return row


def test_a_second_active_publish_to_one_repository_is_refused_by_the_index(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        _publish(db, version_id, build_id)
        db.commit()
        _publish(db, version_id, build_id, status="published", repo="a/b")  # terminal: allowed
        db.commit()
        with pytest.raises(IntegrityError, match="uq_dw_publishes_active_repo"):
            _publish(db, version_id, build_id)
            db.commit()


def test_a_terminal_publish_record_never_changes_and_none_is_deleted(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        live = _publish(db, version_id, build_id)
        done = _publish(db, version_id, build_id, status="published", repo="a/c")
        db.commit()
        live.status = "checking"  # a live row moves
        db.commit()
        done_id, live_id = done.id, live.id
    for sql in (
        f"UPDATE dw_publishes SET commit = '{'d' * 40}' WHERE id = '{done_id}'",
        f"UPDATE dw_publishes SET published_manifest = '{{}}' WHERE id = '{done_id}'",
        f"DELETE FROM dw_publishes WHERE id = '{live_id}'",
    ):
        with pytest.raises(DBAPIError), get_sync_engine().begin() as conn:
            conn.execute(text(sql))


def test_published_requires_its_commit_and_manifest(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        row = _publish(db, version_id, build_id)
        db.commit()
        row.status = "published"
        with pytest.raises(IntegrityError, match="published_is_complete"):
            db.commit()


def test_an_agent_publish_needs_an_approval(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        row = _publish(db, version_id, build_id)
        row.started_by_origin = "agent"
        with pytest.raises(IntegrityError, match="agent_needs_approval"):
            db.commit()


def test_files_configs_and_notes_are_insert_only(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        pub = _publish(db, version_id, build_id)
        db.add(
            PublishFile(
                publish_id=pub.id,
                path_in_repo="README.md",
                role="card",
                bytes=1,
                sha256="a" * 64,
                git_blob_sha1="b" * 40,
                match=True,
            )
        )
        db.add(
            ConfigVersion(
                id="cfg_1",
                kind="selector",
                name="short",
                number=1,
                plugin="rule",
                body={"plugin": "rule", "params": {"max_length": 5}},
                body_sha256="c" * 64,
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.add(
            ModelTermsNote(
                id="mtn_1",
                model_id="autotrust/JEV-9B",
                training_on_outputs="permits",
                text="Apache-2.0 card",
                noted_by="Test Operator",
                noted_by_origin="operator",
            )
        )
        db.commit()
        pub_id = pub.id
    for sql in (
        f"UPDATE dw_publish_files SET match = false WHERE publish_id = '{pub_id}'",
        f"DELETE FROM dw_publish_files WHERE publish_id = '{pub_id}'",
        "UPDATE dw_config_versions SET body_sha256 = repeat('d', 64) WHERE id = 'cfg_1'",
        "DELETE FROM dw_config_versions WHERE id = 'cfg_1'",
        "UPDATE dw_model_terms_notes SET training_on_outputs = 'forbids' WHERE id = 'mtn_1'",
        "DELETE FROM dw_model_terms_notes WHERE id = 'mtn_1'",
    ):
        with pytest.raises(DBAPIError, match="insert-only"), get_sync_engine().begin() as conn:
            conn.execute(text(sql))


def test_an_agent_terms_note_is_refused_by_the_table(clean_db: None) -> None:
    with sync_session_factory()() as db:
        db.add(
            ModelTermsNote(
                id="mtn_2",
                model_id="m",
                training_on_outputs="permits",
                text="t",
                noted_by="agent:x",
                noted_by_origin="agent",
            )
        )
        with pytest.raises(IntegrityError, match="operator_only"):
            db.commit()


def test_a_completed_check_snapshot_never_changes(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version_id, build_id = _version(db)
        run = PublishCheckRun(
            id="pchk_1",
            version_id=version_id,
            build_id=build_id,
            repo_id="a/b",
            requested_visibility="private",
            status="queued",
        )
        db.add(run)
        db.commit()
        run.status, run.results = "completed", [{"check": "C-1"}]
        from src.core.clock import utc_now

        run.completed_at = utc_now()
        db.commit()
    for sql in (
        "UPDATE dw_publish_check_runs SET results = '[]' WHERE id = 'pchk_1'",
        "DELETE FROM dw_publish_check_runs WHERE id = 'pchk_1'",
    ):
        with pytest.raises(DBAPIError), get_sync_engine().begin() as conn:
            conn.execute(text(sql))
