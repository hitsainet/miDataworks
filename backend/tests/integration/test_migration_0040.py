"""Migration 0040 (017 slice 4, FTASKS 7.1) on a POPULATED database.

A whole-dataset Hub source survives 0040 down and up with ``import_slice`` NULL (nothing is
invented); after 0040 two slices of one dataset at one commit are two live sources while the same
slice twice is refused by the identity index; a slice on an upload, or one that is not an object,
is refused at the database; the downgrade refuses while a sliced source is live (the narrower
identity would merge them).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.models.source import Source

COMMIT = hashlib.sha256(b"0040").hexdigest()[:40]


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _source(**over: Any) -> Source:
    fields: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "kind": "hf",
        "state": "ready",
        "display_name": "org/real-text",
        "repo_id": "org/real-text",
        "resolved_commit": COMMIT,
        "licence_raw": "mit",
        "licence_display": "mit",
        "licence_origin": "card_data",
        "library_versions": {"datasets": "t"},
        "created_by": "Test Operator",
        "created_by_origin": "operator",
    }
    fields.update(over)
    return Source(**fields)


def _insert(**over: Any) -> str:
    with sync_session_factory()() as db:
        row = _source(**over)
        db.add(row)
        db.commit()
        return str(row.id)


@pytest.fixture
def whole(clean_db: None) -> Iterator[str]:
    yield _insert()
    command.upgrade(_config(), "head")


def test_0040_round_trips_a_populated_database(whole: str) -> None:
    command.downgrade(_config(), "0039")
    with get_sync_engine().connect() as conn:
        columns = set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": "dw_sources"},
            ).scalars()
        )
    assert "import_slice" not in columns
    command.upgrade(_config(), "head")
    with sync_session_factory()() as db:
        row = db.get(Source, whole)
        assert row is not None and row.import_slice is None and row.state == "ready"


def test_two_slices_are_two_sources_and_one_slice_twice_is_refused(whole: str) -> None:
    _insert(import_slice={"max_rows": 5})
    _insert(import_slice={"max_rows": 6, "data_files": ["a.parquet"]})
    with pytest.raises(IntegrityError):
        _insert(import_slice={"max_rows": 5})
    with pytest.raises(IntegrityError):
        _insert()  # the whole dataset is already live


def test_the_database_refuses_a_slice_that_is_not_a_hub_slice(whole: str) -> None:
    with pytest.raises(IntegrityError):
        _insert(import_slice=[5], resolved_commit=hashlib.sha256(b"x").hexdigest()[:40])
    with pytest.raises(IntegrityError):
        _insert(
            kind="upload",
            repo_id=None,
            resolved_commit=None,
            content_hash="a" * 64,
            import_slice={"max_rows": 1},
        )


def test_the_downgrade_refuses_while_a_sliced_source_is_live(whole: str) -> None:
    _insert(import_slice={"max_rows": 5})
    with pytest.raises(RuntimeError, match="sliced Hub source"):
        command.downgrade(_config(), "0039")
    with get_sync_engine().connect() as conn:
        found = conn.execute(
            text("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_dw_sources_hf_identity' "
                 "AND indexdef LIKE '%import_slice%'")  # fmt: skip
        ).scalar_one()
    assert found == 1, "nothing was dropped"
