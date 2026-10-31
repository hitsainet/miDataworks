"""Migration 0044 and the database's own guarantees (feature 018 slice 4; FTASKS 13.1; FTID §4.2).

``dw_code_rule_uses`` is insert-only (DELETE only under ``dw.purge``) with RESTRICT references; the
live-identity index leaves a ``reuse_blocked`` execution out (decision I7), so a second completed
row of the same identity is admitted only when the first is blocked; the publish role, the build
column, the version flag and the purge kind are admitted. The downgrade refuses while a use row
exists. The migration's literals equal the app's constants.
"""

from __future__ import annotations

import hashlib
import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from src.core.database import get_sync_engine
from src.models.publish import FileRole
from src.models.purge import PURGE_KINDS
from src.models.step_execution import LIVE_WHERE

ROOT = Path(__file__).resolve().parents[3]


def _config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def migration() -> ModuleType:
    path = ROOT / "alembic" / "versions" / "0044_018_code_rule_travel.py"
    spec = importlib.util.spec_from_file_location("m0044", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migrations_literals_are_the_apps_constants() -> None:
    m = migration()
    assert m.NEW_LIVE == LIVE_WHERE
    assert m.NEW_ROLES == "role IN (" + ", ".join(f"'{r.value}'" for r in FileRole) + ")"
    assert m.NEW_PURGE_KINDS == "(" + ", ".join(f"'{k}'" for k in PURGE_KINDS) + ")"


def _sql(statement: str, **params: object) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(text(statement), params)


def _scalar(statement: str, **params: object) -> object:
    with get_sync_engine().connect() as conn:
        return conn.execute(text(statement), params).scalar()


def _job() -> str:
    job = "job_" + uuid.uuid4().hex[:20]
    _sql(
        "INSERT INTO dw_jobs (id, kind, status, progress, params, started_by, started_by_origin) "
        "VALUES (:j, 'version_build', 'completed', 100, '{}'::jsonb, 'Ada', 'operator')",
        j=job,
    )
    return job


def _execution(digest: str, job: str, *, blocked: bool = False) -> str:
    eid = str(uuid.uuid4())
    _sql(
        "INSERT INTO dw_step_executions (id, identity_digest, kind, operator_name, "
        "operator_version, manifest_hash, state, output_dir, job_id, reuse_blocked) VALUES "
        "(:i, :d, 'operator', 'rule_set', '1', :m, 'completed', 'runs/x', :j, :b)",
        i=eid,
        d=digest,
        m="a" * 64,
        j=job,
        b=blocked,
    )
    return eid


def _use_fixture() -> tuple[str, str, str, int]:
    """A step execution, a rule-set and a code-rule version, and an approve row."""
    job = _job()
    execution = _execution("1" * 64, job)
    _sql(
        "INSERT INTO dw_rule_sets (name, behaviour_key, created_by, created_by_origin) "
        "VALUES ('fixture-set', 'fixture-gamma', 'Ada', 'operator')"
    )
    rs_canonical = '{"a":1}'
    _sql(
        "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, body_sha256, "
        "body_canonical, created_by, created_by_origin) VALUES ('rsv_1', 'rule_set', "
        "'fixture-set', 1, 'rule_set', '{}'::jsonb, :s, :c, 'Ada', 'operator')",
        s=hashlib.sha256(rs_canonical.encode()).hexdigest(),
        c=rs_canonical,
    )
    _sql(
        "INSERT INTO dw_code_rules (name, behaviour_key, created_by, created_by_origin) "
        "VALUES ('fixture-rule', 'fixture-gamma', 'Ada', 'operator')"
    )
    cr_canonical = '{"source":"x"}'
    sha = hashlib.sha256(cr_canonical.encode()).hexdigest()
    _sql(
        "INSERT INTO dw_config_versions (id, kind, name, number, plugin, body, body_sha256, "
        "body_canonical, created_by, created_by_origin) VALUES ('crv_1', 'code_rule', "
        "'fixture-rule', 1, 'code_rule', '{}'::jsonb, :s, :c, 'Ada', 'operator')",
        s=sha,
        c=cr_canonical,
    )
    _sql(
        "INSERT INTO dw_code_rule_approvals (code_sha256, action, reason, changed_by, origin) "
        "VALUES (:s, 'approve', 'saved by the operator', 'Ada', 'operator')",
        s=sha,
    )
    approve = int(_scalar("SELECT max(id) FROM dw_code_rule_approvals"))  # type: ignore[arg-type]
    return execution, sha, job, approve


def _use(execution: str, sha: str, approve: int, code_version: str = "crv_1") -> None:
    _sql(
        "INSERT INTO dw_code_rule_uses (step_execution_id, code_sha256, "
        "rule_set_config_version_id, code_rule_config_version_id, runner_contract, "
        "contract_version, bundle_digest, approval_row_id, failed_rows) VALUES "
        "(CAST(:e AS uuid), :s, 'rsv_1', :c, 'midataworks.runner/v1', '1.0', :b, :a, 0)",
        e=execution,
        s=sha,
        c=code_version,
        b="f" * 64,
        a=approve,
    )


def test_use_rows_are_insert_only_and_deleted_only_by_a_purge(clean_db: None) -> None:
    execution, sha, _, approve = _use_fixture()
    _use(execution, sha, approve)
    with pytest.raises(DBAPIError, match="insert-only|append-only|not allowed|immutable"):
        _sql("UPDATE dw_code_rule_uses SET failed_rows = 3")
    with pytest.raises(DBAPIError, match="insert-only|append-only|not allowed|immutable"):
        _sql("DELETE FROM dw_code_rule_uses")
    with get_sync_engine().begin() as conn:
        conn.execute(text("SET LOCAL dw.purge = 'on'"))
        assert conn.execute(text("DELETE FROM dw_code_rule_uses")).rowcount == 1  # type: ignore[attr-defined]


def test_use_rows_restrict_what_they_name(clean_db: None) -> None:
    execution, sha, _, approve = _use_fixture()
    with pytest.raises(IntegrityError, match="fk_dw_code_rule_uses"):
        _use(execution, sha, approve, code_version="crv_missing")
    _use(execution, sha, approve)
    with get_sync_engine().begin() as conn:
        conn.execute(text("SET LOCAL dw.purge = 'on'"))
        with pytest.raises(IntegrityError, match="fk_dw_code_rule_uses"):
            conn.execute(text("DELETE FROM dw_config_versions WHERE id = 'crv_1'"))


def test_a_reuse_blocked_completion_leaves_the_live_identity_free_i7(clean_db: None) -> None:
    job = _job()
    _execution("2" * 64, job, blocked=True)
    _execution("2" * 64, job)  # admitted: the first is blocked, so it is not live
    with pytest.raises(IntegrityError, match="uq_dw_step_executions_identity_live"):
        _execution("2" * 64, job)  # a second live one is not


def test_the_new_role_column_flag_and_purge_kind_are_admitted(clean_db: None) -> None:
    for check, wanted in (
        ("ck_dw_publish_files_role_valid", "code_rule"),
        ("ck_dw_purge_events_entity_kind_valid", "code_rule"),
    ):
        found = str(
            _scalar(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :c", c=check
            )
        )
        assert wanted in found, (check, found)
    for table, column in (
        ("dw_publish_builds", "code_rule_files"),
        ("dw_versions", "code_rules_recorded"),
        ("dw_step_executions", "reuse_blocked"),
    ):
        assert _scalar(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = :t "
            "AND column_name = :c",
            t=table,
            c=column,
        ), (table, column)


def test_downgrade_and_upgrade_on_an_empty_store_and_a_refusal_while_used(clean_db: None) -> None:
    command.downgrade(_config(), "0043")
    try:
        assert _scalar("SELECT to_regclass('dw_code_rule_uses')") is None
        assert not _scalar(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = "
            "'dw_step_executions' AND column_name = 'reuse_blocked'"
        )
    finally:
        command.upgrade(_config(), "head")
    execution, sha, _, approve = _use_fixture()
    _use(execution, sha, approve)
    with pytest.raises(RuntimeError, match="build record"):
        command.downgrade(_config(), "0043")
    assert _scalar("SELECT count(*) FROM dw_code_rule_uses") == 1


def test_downgrade_refuses_while_a_step_is_reuse_blocked(clean_db: None) -> None:
    _execution("3" * 64, _job(), blocked=True)
    with pytest.raises(RuntimeError, match="reuse-blocked"):
        command.downgrade(_config(), "0043")
    assert _scalar("SELECT to_regclass('dw_code_rule_uses')") is not None
