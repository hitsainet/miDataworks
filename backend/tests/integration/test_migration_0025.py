"""Migration 0025 (feature 011): ``commit_check`` and ``provenance`` round-trip up/down/up, carry
no default and no backfill (every existing row reads NULL: "not checked (sent before feature
011)" / "not recorded"), and the check admits only ``verified`` and ``claimed`` (FR-011.7)."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from src.core.database import get_sync_engine


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _column(table: str, column: str) -> tuple[str, str | None] | None:
    with get_sync_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).one_or_none()
    return None if row is None else (str(row[0]), row[1])


def _check() -> str | None:
    with get_sync_engine().connect() as conn:
        found: str | None = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_dw_detector_send_steps_commit_check_valid'"
            )
        ).scalar_one_or_none()
    return found


def test_0025_round_trips_with_no_default(clean_db: None) -> None:
    config = _config()
    try:
        command.downgrade(config, "0024")
        assert _column("dw_detector_send_steps", "commit_check") is None
        assert _column("dw_detector_results", "provenance") is None
        assert _check() is None
    finally:
        command.upgrade(config, "head")
    assert _column("dw_detector_send_steps", "commit_check") == ("YES", None)
    assert _column("dw_detector_results", "provenance") == ("YES", None)
    check = _check()
    assert check is not None
    assert "'verified'" in check and "'claimed'" in check and "IS NULL" in check
