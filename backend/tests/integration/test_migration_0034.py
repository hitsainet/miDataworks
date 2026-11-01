"""Migration 0034 (feature 014): the endpoint keyring and the Settings PIN tables.

Round-trips down and up; on the way up nothing is invented (no parked key, no PIN: Settings stays
open until the operator sets one); the keyring's rows go when their role row goes."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine


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
                    "('dw_endpoint_role_keys', 'dw_settings_pin')"
                )
            ).scalars()
        )


def test_0034_round_trips_and_starts_empty(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0033")
        assert _tables() == set()
    finally:
        command.upgrade(config, "head")
    assert _tables() == {"dw_endpoint_role_keys", "dw_settings_pin"}
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_endpoint_role_keys")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM dw_settings_pin")).scalar() == 0


def test_0034_keys_go_with_their_role_and_the_pin_is_one_row(clean_db: None) -> None:
    engine = get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_endpoint_roles (role, inherit_from_judge, use_mode) "
                "VALUES ('classifier', false, 'own')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO dw_endpoint_role_keys (role, origin, api_key_ciphertext) "
                "VALUES ('classifier', 'https://api.typesafe.test:443', 'x')"
            )
        )
        conn.execute(text("DELETE FROM dw_endpoint_roles WHERE role = 'classifier'"))
        assert conn.execute(text("SELECT count(*) FROM dw_endpoint_role_keys")).scalar() == 0
        conn.execute(text("INSERT INTO dw_settings_pin (id, pin_hash) VALUES (1, 'h')"))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("INSERT INTO dw_settings_pin (id, pin_hash) VALUES (2, 'h')"))
