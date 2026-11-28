"""Migration 0049 (021 FTASKS 8.3, 8.4; FR-021.18; FTDD §4.4): two columns, one CHECK, no backfill.

The immutability trigger (0008) compares whole rows, so the new columns are immutable from the
first insert: an ``UPDATE`` of either on a completed version is refused. A downgrade, then an
upgrade, leaves the trigger in place; the downgrade refuses while a version carries a sensitivity.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from tests.support import db_factories


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


@pytest.fixture
def version_ids(clean_db: None) -> Iterator[dict[str, str]]:
    with sync_session_factory()() as db:
        plain = db_factories.version(db)
        declared = db_factories.version(
            db, sensitivity="sensitive", sensitivity_basis={"floor": "sensitive"}
        )
        db.commit()
        ids = {"plain": str(plain.id), "declared": str(declared.id)}
    yield ids
    command.upgrade(_config(), "head")


def _update(column: str, value: str, version_id: str) -> None:
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(f"UPDATE dw_versions SET {column} = :v WHERE id = :i"),  # noqa: S608
            {"v": value, "i": version_id},
        )


@pytest.mark.parametrize(
    ("column", "value"), [("sensitivity", "sensitive"), ("sensitivity_basis", '{"floor": null}')]
)
def test_trigger_refuses_update(version_ids: dict[str, str], column: str, value: str) -> None:
    """No backfill can set it, and nothing can lower it later (FTDD §4.4)."""
    with pytest.raises(DBAPIError, match="immutable"):
        _update(column, value, version_ids["plain"])


def test_the_database_refuses_another_value(version_ids: dict[str, str]) -> None:
    with sync_session_factory()() as db, pytest.raises(IntegrityError, match="sensitivity_valid"):
        db_factories.version(db, sensitivity="private_only")
        db.commit()


def test_down_then_up_keeps_the_trigger_and_the_downgrade_refuses_while_used(
    version_ids: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError, match="carry a sensitivity"):
        command.downgrade(_config(), "0048")
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("SET LOCAL dw.purge = 'on'")
        )  # the purge flag (0032) lets a delete through
        conn.execute(text("DELETE FROM dw_versions WHERE sensitivity IS NOT NULL"))
    command.downgrade(_config(), "0048")
    command.upgrade(_config(), "head")
    with pytest.raises(DBAPIError, match="immutable"):
        _update("sensitivity", "sensitive", version_ids["plain"])
    with get_sync_engine().connect() as conn:
        value = conn.execute(
            text("SELECT sensitivity FROM dw_versions WHERE id = :i"), {"i": version_ids["plain"]}
        ).scalar_one()
    assert value is None  # no backfill
