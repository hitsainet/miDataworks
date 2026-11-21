"""The allowlist table and service (FR-003.11, P-09; FTASKS 5.1, 5.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.operators import allowlist
from src.operators.errors import OperatorError

TRIPLE = ("acme-ops", "1.0", "tagger")


def test_history_is_kept_and_current_state_is_the_latest(clean_db: None) -> None:
    with sync_session_factory()() as session:
        allowlist.allow(session, TRIPLE, "reviewed the code", "Test Operator")
        assert allowlist.allowed_triples(session) == {TRIPLE}
        allowlist.revoke(session, TRIPLE, "found a problem", "Test Operator")
        assert allowlist.allowed_triples(session) == set()
        allowlist.allow(session, TRIPLE, "fixed upstream", "Another Operator")
        assert allowlist.allowed_triples(session) == {TRIPLE}
        rows = allowlist.history(session)
    assert [r.action for r in rows] == ["allow", "revoke", "allow"]
    assert rows[0].changed_by == "Another Operator" and rows[2].reason == "reviewed the code"
    assert allowlist.read_allowed() == {TRIPLE}


def test_a_new_distribution_version_is_not_allowed(clean_db: None) -> None:
    with sync_session_factory()() as session:
        allowlist.allow(session, TRIPLE, "reviewed", "Test Operator")
        assert ("acme-ops", "1.1", "tagger") not in allowlist.allowed_triples(session)


def test_reason_and_who_are_required(clean_db: None) -> None:
    with sync_session_factory()() as session:
        with pytest.raises(OperatorError) as exc:
            allowlist.allow(session, TRIPLE, "  ", "Test Operator")
        assert exc.value.code == "reason_required"
        with pytest.raises(OperatorError):
            allowlist.allow(session, TRIPLE, "ok", "")


def test_the_database_refuses_an_agent_origin(clean_db: None) -> None:
    """P-09 as a schema fact: even a direct insert cannot record an agent's change."""
    with pytest.raises(IntegrityError, match="origin_operator_only"):
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dw_operator_allowlist (distribution, distribution_version, "
                    "entry_point, action, reason, changed_by, origin) VALUES "
                    "('d', '1', 'e', 'allow', 'r', 'agent:x', 'agent')"
                )
            )


def test_the_database_refuses_an_unknown_action(clean_db: None) -> None:
    with pytest.raises(IntegrityError, match="action_valid"):
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO dw_operator_allowlist (distribution, distribution_version, "
                    "entry_point, action, reason, changed_by, origin) VALUES "
                    "('d', '1', 'e', 'maybe', 'r', 'w', 'operator')"
                )
            )


def _config() -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def test_migration_0010_downgrades_and_upgrades(clean_db: None) -> None:
    config = _config()

    def tables() -> set[str]:
        with get_sync_engine().connect() as conn:
            return set(
                conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).scalars()
            )

    try:
        command.downgrade(config, "0009")
        assert "dw_operator_allowlist" not in tables()
        assert "dw_agent_label_rows" in tables()
    finally:
        command.upgrade(config, "head")
    assert "dw_operator_allowlist" in tables()
