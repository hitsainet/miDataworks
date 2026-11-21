"""Migration 0048 (feature 020, FTID §4.1): the old-name evidence tables.

Round-trips down and up and starts empty; the key is ``(kind, old_name)`` and ``kind`` is closed
(``env`` only in miDataworks); a write-error row is one per reporter."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine

TABLES = {"dw_legacy_name_uses", "dw_legacy_name_write_errors"}


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _tables() -> set[str]:
    with get_sync_engine().connect() as conn:
        return set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_name IN "
                    "('dw_legacy_name_uses', 'dw_legacy_name_write_errors')"
                )
            ).scalars()
        )


def test_upgrade_creates_both_tables_and_downgrade_drops_them(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0047")
        assert _tables() == set()
    finally:
        command.upgrade(config, "head")
    assert _tables() == TABLES
    with get_sync_engine().connect() as conn:
        for table in TABLES:
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_the_key_is_kind_and_old_name_and_kind_is_closed(clean_db: None) -> None:
    engine = get_sync_engine()
    insert = text(
        "INSERT INTO dw_legacy_name_uses (kind, old_name, new_name, last_seen_at) "
        "VALUES (:k, 'MILLM_BASE_URL', 'MISERVER_BASE_URL', now())"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"k": "env"})
        row = conn.execute(text("SELECT count, first_seen_at FROM dw_legacy_name_uses")).one()
        assert row.count == 1 and row.first_seen_at is not None
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"k": "env"})
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"k": "host"})
