"""Migration ``0036`` and the database's own guards (FR-016.16, FR-016.20, FR-016.39; FTID 016 section 4.1).

- the migration reverses on an empty database and refuses while a use record exists;
- an UPDATE of a stored version is refused; a DELETE is refused outside ``dw.purge``;
- a ``rule_set`` row with no head, or whose ``body_canonical`` does not hash to ``body_sha256``,
  is refused;
- the behaviour-key filter uses ``ix_dw_rule_sets_behaviour_key`` on 10,000 keys (the planner's
  own choice after ``ANALYZE``; no ``enable_seqscan = off``).
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from alembic import command
from src.core.database import get_sync_engine

DOC = '{"a":1}'
SHA = hashlib.sha256(DOC.encode()).hexdigest()


def _config() -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _objects() -> dict[str, set[str]]:
    """Tables, triggers on dw_config_versions, the three kind CHECKs (by name) and columns."""
    with get_sync_engine().connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename IN "
                    "('dw_rule_sets', 'dw_rule_set_uses')"
                )
            ).scalars()
        )
        triggers = set(
            conn.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid = "
                    "'dw_config_versions'::regclass"
                )
            ).scalars()
        )
        checks = set(
            conn.execute(
                text(
                    "SELECT conname || ': ' || pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname IN "
                    "('ck_dw_config_versions_kind_valid', 'ck_dw_archive_events_entity_kind_valid',"
                    " 'ck_dw_purge_events_entity_kind_valid')"
                )
            ).scalars()
        )
        columns = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'dw_config_versions'"
                )
            ).scalars()
        )
    return {"tables": tables, "triggers": triggers, "checks": checks, "columns": columns}


def _head(conn: object, name: str = "rs-a", key: str = "key-a") -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO dw_rule_sets (name, behaviour_key, created_by, created_by_origin) "
            "VALUES (:n, :k, 'Test Operator', 'operator')"
        ),
        {"n": name, "k": key},
    )


def _version(conn: object, vid: str = "rsv_1", name: str = "rs-a", body: str = DOC) -> None:
    conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, body_sha256, "
            "body_canonical, created_by, created_by_origin) VALUES (:id, 'rule_set', :n, 1, "
            "'rule_set', CAST(:b AS jsonb), :s, :c, 'Test Operator', 'operator')"
        ),
        {"id": vid, "n": name, "b": DOC, "s": SHA, "c": body},
    )


def test_0036_round_trips_on_an_empty_database(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0035")
        down = _objects()
        assert down["tables"] == set()
        assert "body_canonical" not in down["columns"]
        assert {"dw_config_versions_insert_only"} == {
            t for t in down["triggers"] if "insert_only" in t or "rule_set" in t
        }
        assert all("rule_set" not in c for c in down["checks"]), down["checks"]
    finally:
        command.upgrade(config, "head")
    up = _objects()
    assert up["tables"] == {"dw_rule_sets", "dw_rule_set_uses"}
    assert "body_canonical" in up["columns"]
    assert {
        "dw_config_versions_insert_only",
        "dw_config_versions_insert_only_delete",
        "dw_config_versions_rule_set_head",
    } <= up["triggers"]
    assert len([c for c in up["checks"] if "rule_set" in c]) == 3


def test_0036_downgrade_refuses_while_a_use_record_exists(clean_db: None) -> None:
    engine = get_sync_engine()
    execution = str(uuid.uuid4())
    with engine.begin() as conn:
        _head(conn)
        _version(conn)
        conn.execute(
            text(
                "INSERT INTO dw_jobs (id, kind, status, progress, params, started_by, "
                "started_by_origin) VALUES ('job_u', 'version_build', 'completed', 1, '{}', "
                "'Test Operator', 'operator')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dw_step_executions (id, identity_digest, kind, state, job_id, "
                "output_dir, operator_name, operator_version, manifest_hash) VALUES (:e, :d, "
                "'operator', 'completed', 'job_u', 'runs/x', 'rule_set', '1', :d)"
            ),
            {"e": execution, "d": "c" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO dw_rule_set_uses (step_execution_id, config_version_id, "
                "rule_set_sha256, behaviour_key, use) VALUES (:e, 'rsv_1', :s, 'key-a', 'label')"
            ),
            {"e": execution, "s": SHA},
        )
    with pytest.raises(RuntimeError, match="refusing to downgrade 0036: dw_rule_set_uses holds 1"):
        command.downgrade(_config(), "0035")
    assert _objects()["tables"] == {"dw_rule_sets", "dw_rule_set_uses"}


def test_a_stored_version_is_never_updated_and_deleted_only_under_purge(clean_db: None) -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        _head(conn)
        _version(conn)
    with pytest.raises(DBAPIError, match="insert-only"), engine.begin() as conn:
        conn.execute(text("UPDATE dw_config_versions SET plugin = 'x' WHERE id = 'rsv_1'"))
    with pytest.raises(DBAPIError, match="insert-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM dw_config_versions WHERE id = 'rsv_1'"))
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL dw.purge = 'on'"))
        assert conn.execute(text("DELETE FROM dw_config_versions WHERE id = 'rsv_1'")).rowcount == 1


def test_a_rule_set_version_needs_its_head(clean_db: None) -> None:
    with pytest.raises(DBAPIError, match="has no head row"), get_sync_engine().begin() as conn:
        _version(conn, name="headless")


def test_body_canonical_must_hash_to_body_sha256(clean_db: None) -> None:
    with get_sync_engine().begin() as conn:
        _head(conn)
    with pytest.raises(DBAPIError, match="canonical_sha"), get_sync_engine().begin() as conn:
        _version(conn, body='{"a": 1}')


def test_a_rule_set_version_must_carry_its_canonical_text(clean_db: None) -> None:
    with get_sync_engine().begin() as conn:
        _head(conn)
    with (
        pytest.raises(DBAPIError, match="rule_set_canonical_present"),
        get_sync_engine().begin() as conn,
    ):
        conn.execute(
            text(
                "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, "
                "body_sha256, created_by, created_by_origin) VALUES ('rsv_2', 'rule_set', 'rs-a',"
                " 1, 'rule_set', '{}', :s, 'Test Operator', 'operator')"
            ),
            {"s": SHA},
        )


def test_a_use_record_is_insert_only(clean_db: None) -> None:
    engine = get_sync_engine()
    execution = str(uuid.uuid4())
    with engine.begin() as conn:
        _head(conn)
        _version(conn)
        conn.execute(
            text(
                "INSERT INTO dw_jobs (id, kind, status, progress, params, started_by, "
                "started_by_origin) VALUES ('job_u', 'version_build', 'completed', 1, '{}', "
                "'Test Operator', 'operator')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dw_step_executions (id, identity_digest, kind, state, job_id, "
                "output_dir, operator_name, operator_version, manifest_hash) VALUES (:e, :d, "
                "'operator', 'completed', 'job_u', 'runs/x', 'rule_set', '1', :d)"
            ),
            {"e": execution, "d": "c" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO dw_rule_set_uses (step_execution_id, config_version_id, "
                "rule_set_sha256, behaviour_key, use) VALUES (:e, 'rsv_1', :s, 'key-a', 'label')"
            ),
            {"e": execution, "s": SHA},
        )
    with pytest.raises(DBAPIError, match="insert-only"), engine.begin() as conn:
        conn.execute(text("UPDATE dw_rule_set_uses SET use = 'veto'"))
    with pytest.raises(DBAPIError, match="insert-only"), engine.begin() as conn:
        conn.execute(text("DELETE FROM dw_rule_set_uses"))
    with pytest.raises(DBAPIError, match="violates foreign key"), engine.begin() as conn:
        conn.execute(text("SET LOCAL dw.purge = 'on'"))
        conn.execute(text("DELETE FROM dw_config_versions WHERE id = 'rsv_1'"))


def test_the_behaviour_filter_uses_its_index_on_ten_thousand_keys(clean_db: None) -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_rule_sets (name, behaviour_key, created_by, created_by_origin) "
                "SELECT 'rs-' || g, 'key-' || g, 'Test Operator', 'operator' "
                "FROM generate_series(1, 10000) g"
            )
        )
        conn.execute(text("ANALYZE dw_rule_sets"))
        plan = "\n".join(
            conn.execute(
                text(
                    "EXPLAIN SELECT * FROM dw_rule_sets WHERE behaviour_key = 'key-5000' "
                    "AND archived_at IS NULL ORDER BY name LIMIT 50"
                )
            ).scalars()
        )
    assert "ix_dw_rule_sets_behaviour_key" in plan, plan


def test_0037_round_trips_and_marks_existing_versions_not_recorded(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0036")
        with get_sync_engine().connect() as conn:
            cols = set(
                conn.execute(
                    text(
                        "SELECT table_name || '.' || column_name FROM information_schema.columns "
                        "WHERE column_name IN ('rule_set_files', 'rule_sets_recorded')"
                    )
                ).scalars()
            )
        assert cols == set()
    finally:
        command.upgrade(config, "head")
    with get_sync_engine().connect() as conn:
        default = conn.execute(
            text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_name = 'dw_versions' AND column_name = 'rule_sets_recorded'"
            )
        ).scalar_one()
        role = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_dw_publish_files_role_valid'"
            )
        ).scalar_one()
    assert default == "true", "versions built from now on are recorded"
    assert "rule_set" in role
