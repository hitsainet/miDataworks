"""Migration 0043 and the database's own guarantees (feature 018; FTASKS 9.2, 9.3; FR-018.26, 029).

What must hold under ANY code path is enforced by PostgreSQL, so each is tried here with raw SQL:
an agent-origin approval row (M14), an UPDATE or DELETE of the approval list, a ``code_rule``
version without its head, stored bytes that do not hash to ``body_sha256``, a ``code_rule`` row
without its canonical text. The downgrade refuses while an approval row exists. The migration's
literals equal the app's constants.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from src.core.database import get_sync_engine
from src.models.archive import ARCHIVE_KINDS
from src.models.publish import ConfigKind
from src.rules.rule_set import NAME_PATTERN
from src.schemas.behaviour import BEHAVIOUR_KEY_PATTERN

ROOT = Path(__file__).resolve().parents[3]
SHA = "c" * 64


def _config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def migration() -> ModuleType:
    path = ROOT / "alembic" / "versions" / "0043_018_code_rules.py"
    spec = importlib.util.spec_from_file_location("m0043", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrations_literals_are_the_apps_constants() -> None:
    m = migration()
    assert m.NAME == NAME_PATTERN
    assert m.SLUG == BEHAVIOUR_KEY_PATTERN
    kinds = "kind IN (" + ", ".join(f"'{k.value}'" for k in ConfigKind) + ")"
    assert m.NEW_CONFIG_KINDS == kinds
    assert m.NEW_ARCHIVE_KINDS == "(" + ", ".join(f"'{k}'" for k in ARCHIVE_KINDS) + ")"


def _sql(statement: str, **params: object) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(text(statement), params)


def _approval(origin: str = "operator", action: str = "approve") -> None:
    _sql(
        "INSERT INTO dw_code_rule_approvals (code_sha256, action, reason, changed_by, origin) "
        "VALUES (:s, :a, 'r', 'Ada', :o)",
        s=SHA,
        a=action,
        o=origin,
    )


def _head(name: str = "fixture-rule") -> None:
    _sql(
        "INSERT INTO dw_code_rules (name, behaviour_key, created_by, created_by_origin) "
        "VALUES (:n, 'fixture-gamma', 'Ada', 'operator')",
        n=name,
    )


def _version(name: str, canonical: str | None, sha: str, plugin: str = "code_rule") -> None:
    _sql(
        "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, body_sha256, "
        "body_canonical, created_by, created_by_origin) VALUES "
        "(:i, 'code_rule', :n, 1, :p, '{}'::jsonb, :s, :c, 'Ada', 'operator')",
        i="crv_" + sha[:20],
        n=name,
        p=plugin,
        s=sha,
        c=canonical,
    )


def test_an_agent_origin_approval_is_refused_by_the_database_m14(clean_db: None) -> None:
    with pytest.raises(IntegrityError, match="origin_operator_only"):
        _approval(origin="agent")
    _approval()  # the operator's is admitted
    with pytest.raises(IntegrityError, match="action_valid"):
        _approval(action="allow")


def test_the_approval_list_and_contract_runs_are_insert_only(clean_db: None) -> None:
    _approval()
    _sql(
        "INSERT INTO dw_code_rule_contract_runs (code_sha256, contract_version, bundle_digest, "
        "passed) VALUES (:s, '1.0', 'd', true)",
        s=SHA,
    )
    for table in ("dw_code_rule_approvals", "dw_code_rule_contract_runs"):
        with pytest.raises(DBAPIError, match="insert-only|append-only|not allowed|immutable"):
            _sql(f"UPDATE {table} SET code_sha256 = :s", s="d" * 64)  # noqa: S608
        with pytest.raises(DBAPIError, match="insert-only|append-only|not allowed|immutable"):
            _sql(f"DELETE FROM {table}")  # noqa: S608
    with pytest.raises(IntegrityError, match="failure_named"):
        _sql(
            "INSERT INTO dw_code_rule_contract_runs (code_sha256, contract_version, "
            "bundle_digest, passed) VALUES (:s, '1.0', 'd', false)",
            s=SHA,
        )


def test_a_code_rule_version_needs_its_head_and_hashed_canonical_bytes(clean_db: None) -> None:
    import hashlib

    canonical = '{"source":"x"}'
    sha = hashlib.sha256(canonical.encode()).hexdigest()
    with pytest.raises(DBAPIError, match="no head row"):
        _version("no-head", canonical, sha)
    _head("fixture-rule")
    with pytest.raises(IntegrityError, match="canonical_sha"):
        _version("fixture-rule", canonical, "e" * 64)  # bytes that do not hash to body_sha256
    with pytest.raises(IntegrityError, match="code_rule_canonical_present"):
        _version("fixture-rule", None, sha)
    with pytest.raises(IntegrityError, match="code_rule_canonical_present"):
        _version("fixture-rule", canonical, sha, plugin="rule_set")
    _version("fixture-rule", canonical, sha)
    with pytest.raises(DBAPIError):
        _sql("UPDATE dw_config_versions SET body = '{\"x\": 1}'::jsonb WHERE kind = 'code_rule'")


def test_an_archive_event_may_name_a_code_rule(clean_db: None) -> None:
    _sql(
        "INSERT INTO dw_archive_events (id, entity_kind, entity_id, action, actor, actor_origin) "
        "VALUES ('arc_1', 'code_rule', 'fixture-rule', 'archive', 'Ada', 'operator')"
    )
    # 0043 left the purge tombstone CHECK unchanged; 0044 (slice 4) admits code_rule there
    # (test_migration_0044.py).


def test_downgrade_and_upgrade_on_an_empty_store_and_a_refusal_while_approved(
    clean_db: None,
) -> None:
    command.downgrade(_config(), "0042")
    try:
        with get_sync_engine().connect() as conn:
            assert conn.execute(text("SELECT to_regclass('dw_code_rules')")).scalar() is None
    finally:
        command.upgrade(_config(), "head")
    _approval()
    with pytest.raises(RuntimeError, match="operator decision"):
        command.downgrade(_config(), "0042")
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_code_rule_approvals")).scalar() == 1


def test_downgrade_refuses_while_an_archive_record_names_a_code_rule(clean_db: None) -> None:
    """018 Review R2: the second refusal, alone (no approval row): the restored CHECK would not
    admit the record, so the downgrade names it instead of failing on the constraint."""
    _sql(
        "INSERT INTO dw_archive_events (id, entity_kind, entity_id, action, actor, actor_origin) "
        "VALUES ('arc_cr', 'code_rule', 'fixture-rule', 'archive', 'Ada', 'operator')"
    )
    with pytest.raises(RuntimeError, match="archive record"):
        command.downgrade(_config(), "0042")
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT to_regclass('dw_code_rules')")).scalar() is not None


def test_a_populated_round_trip_keeps_every_other_row(clean_db: None) -> None:
    """018 Review R2: downgrade and upgrade over a store that holds other configuration, other
    archive records and code-rule versions with no decision yet: the code-rule rows go with the
    kind (under ``dw.purge``), nothing else is touched, and the upgraded schema works again."""
    import hashlib

    canonical = '{"source":"y"}'
    sha = hashlib.sha256(canonical.encode()).hexdigest()
    _sql(
        "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, body_sha256, "
        "created_by, created_by_origin) VALUES ('cfg_sel_1', 'selector', 'keep-me', 1, "
        "'heuristic_filter', '{\"a\": 1}'::jsonb, :s, 'Ada', 'operator')",
        s="b" * 64,
    )
    _sql(
        "INSERT INTO dw_archive_events (id, entity_kind, entity_id, action, actor, actor_origin) "
        "VALUES ('arc_rs', 'rule_set', 'some-set', 'archive', 'Ada', 'operator')"
    )
    _head("fixture-rule")
    _version("fixture-rule", canonical, sha)
    _sql(
        "INSERT INTO dw_code_rule_contract_runs (code_sha256, contract_version, bundle_digest, "
        "passed) VALUES (:s, '1.0', 'd', true)",
        s=sha,
    )
    command.downgrade(_config(), "0042")
    try:
        with get_sync_engine().connect() as conn:
            kinds = conn.execute(text("SELECT kind, name FROM dw_config_versions")).all()
            assert [tuple(k) for k in kinds] == [("selector", "keep-me")]
            assert conn.execute(text("SELECT count(*) FROM dw_archive_events")).scalar() == 1
            assert (
                conn.execute(text("SELECT to_regclass('dw_code_rule_contract_runs')")).scalar()
                is None
            )
    finally:
        command.upgrade(_config(), "head")
    _head("fixture-rule")
    _version("fixture-rule", canonical, sha)
    _approval()
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_config_versions")).scalar() == 2
