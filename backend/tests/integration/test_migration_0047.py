"""Migration 0047 (ENH-006 G21) on a populated database: a set names the sibling it borrows from;
the database refuses a set borrowing from itself or from a set that does not exist, and refuses to
remove a set that is borrowed from. The downgrade drops the column and nothing else."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.models.detector_set import DetectorSet


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


@pytest.fixture
def at_head(clean_db: None) -> Iterator[None]:
    yield
    command.upgrade(_config(), "head")


def _set(set_id: str, borrows: str | None = None) -> DetectorSet:
    return DetectorSet(
        id=set_id,
        name=set_id.replace("_", "-"),
        created_by="Test Operator",
        created_by_origin="operator",
        borrows_from_set_id=borrows,
    )


def test_the_column_its_check_and_its_foreign_key(at_head: None) -> None:
    with sync_session_factory()() as db:
        db.add(_set("dts_a"))
        db.commit()
        db.add(_set("dts_b", borrows="dts_a"))
        db.commit()
        db.add(_set("dts_self", borrows="dts_self"))
        with pytest.raises(IntegrityError, match="borrow_not_self"):
            db.commit()
        db.rollback()
        db.add(_set("dts_c", borrows="dts_nowhere"))
        with pytest.raises(IntegrityError, match="borrows_from_set_id"):
            db.commit()
        db.rollback()
    with pytest.raises(IntegrityError), get_sync_engine().begin() as conn:
        conn.execute(text("DELETE FROM dw_detector_sets WHERE id = 'dts_a'"))


def test_the_downgrade_drops_only_the_column(at_head: None) -> None:
    with sync_session_factory()() as db:
        db.add(_set("dts_a"))
        db.commit()
        db.add(_set("dts_b", borrows="dts_a"))
        db.commit()
    command.downgrade(_config(), "0046")
    with get_sync_engine().connect() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("dw_detector_sets")}
        kept = conn.execute(text("SELECT id FROM dw_detector_sets ORDER BY id")).scalars().all()
    assert "borrows_from_set_id" not in columns
    assert kept == ["dts_a", "dts_b"]
