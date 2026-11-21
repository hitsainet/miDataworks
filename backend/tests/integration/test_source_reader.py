"""The reader API features 002 and 008 use (001 FTASKS 9.6; FR-001.5, FR-001.6, FR-001.31).

Each reader refuses a source that is not ``ready``; a ready one reads its pin, files and detection;
the locator is ``"<split>:<row_index>"``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import async_session_factory, dispose_engines, sync_session_factory
from src.core.errors import AppError
from src.services.sources import reader
from tests.support import db_factories


@pytest.fixture
async def db(clean_db: None) -> AsyncIterator[AsyncSession]:
    async with async_session_factory()() as session:
        yield session
    await dispose_engines()


def make(state: str) -> str:
    with sync_session_factory()() as s:
        row = db_factories.source(s, state=state, detection={"suggested_target": "detector"})
        db_factories.source_file(s, row, "train", f"sources/{row.id}/train.parquet", "a" * 64, 3, 9)
        s.commit()
        return row.id


@pytest.mark.parametrize("state", ["importing", "failed", "cancelled", "deleted"])
async def test_every_reader_refuses_a_source_that_is_not_ready(
    db: AsyncSession, state: str
) -> None:
    source_id = make(state)
    for call in (reader.get_ready_source, reader.list_files):
        with pytest.raises(AppError) as info:
            await call(db, source_id)
        assert info.value.code == "source_not_ready"
    with sync_session_factory()() as s:
        for sync_call in (reader.get_ready_source_sync, reader.list_files_sync):
            with pytest.raises(AppError):
                sync_call(s, source_id)


async def test_a_ready_source_reads_its_pin_files_and_detection(db: AsyncSession) -> None:
    source_id = make("ready")
    ready = await reader.get_ready_source(db, source_id)
    assert ready.pin == {"revision": "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac"}
    assert reader.detection(ready) == {"suggested_target": "detector"}
    [f] = await reader.list_files(db, source_id)
    assert (f.split, f.rows, f.sha256) == ("train", 3, "a" * 64)


async def test_an_unknown_or_malformed_id_is_not_found(db: AsyncSession) -> None:
    for bad in ("not-a-uuid", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises(AppError) as info:
            await reader.get_ready_source(db, bad)
        assert info.value.code == "source_not_found"


def test_the_locator_format() -> None:
    assert reader.locator("train", 0) == "train:0"
    assert reader.locator("dev 2023/v1", 41) == "dev 2023/v1:41"
