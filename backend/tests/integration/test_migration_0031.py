"""Migration 0031: an existing key is bound to its row's CURRENT base URL (operator decision
2026-10-20), so it keeps working where it is configured and goes nowhere else; a key with no usable
base URL stays unbound (never sent); a row without a key gets no origin. Round-trips down/up."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from src.core.database import get_sync_engine
from src.core.encryption import encrypt_value
from src.services.endpoint_resolver import resolve

KEY = "sk-before-0031-0123456789"


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _has_column() -> bool:
    with get_sync_engine().connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'dw_endpoint_roles' AND column_name = 'api_key_origin'"
                )
            ).scalar()
            is not None
        )


def test_0031_binds_existing_keys_to_their_current_url(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0030")
        assert not _has_column()
        rows = [
            ("classifier", "openai_scoring", "HTTP://Millm.Test/v1", "m", encrypt_value(KEY)),
            ("judge", "openai_chat", "https://judge.test:8443/v1", "q", encrypt_value(KEY)),
            ("generation", "openai_chat", None, "g", encrypt_value(KEY)),
            ("embeddings", "openai_embeddings", "http://embed.test/v1", "e", None),
        ]
        with get_sync_engine().begin() as conn:
            for role, protocol, base_url, model, cipher in rows:
                conn.execute(
                    text(
                        "INSERT INTO dw_endpoint_roles (role, protocol, base_url, model_id, "
                        "api_key_ciphertext, inherit_from_judge, use_mode) "
                        "VALUES (:r, :p, :u, :m, :c, false, 'own')"
                    ),
                    {"r": role, "p": protocol, "u": base_url, "m": model, "c": cipher},
                )
    finally:
        command.upgrade(config, "head")
    with get_sync_engine().connect() as conn:
        got: dict[str, str | None] = dict(
            conn.execute(text("SELECT role, api_key_origin FROM dw_endpoint_roles")).tuples().all()
        )
    assert got == {
        "classifier": "http://millm.test:80",
        "judge": "https://judge.test:8443",
        "generation": None,
        "embeddings": None,
    }
    # And the key still resolves where it was configured (the backfill is what makes it so).
    assert resolve("classifier").api_key == KEY
    assert resolve("judge").api_key == KEY


def test_0031_round_trips(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0030")
        assert not _has_column()
    finally:
        command.upgrade(config, "head")
    assert _has_column()
