"""Delete as tombstone (001 FTASKS 9.4, 9.5; FR-001.36, P-15).

A source a version read is refused naming the versions; one still importing is refused; an
unreferenced one becomes ``deleted`` with its files gone and its record and hashes kept; a failed
unlink leaves it ``ready``; deleting is not gated for agents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.agent_origin import Who
from src.core.database import async_session_factory, dispose_engines
from src.core.errors import AppError
from src.services.sources import source_service
from tests.support.source_fixtures import file_rows, source_row
from tests.support.version_fixtures import HUMOR_TRAIN, driver, make_source

__all__ = ["driver"]

OPERATOR = Who("Test Operator", "operator")


@pytest.fixture
async def db(clean_db: None) -> AsyncIterator[AsyncSession]:
    async with async_session_factory()() as session:
        yield session
    await dispose_engines()


async def test_an_unreferenced_source_is_tombstoned_its_files_removed_its_record_kept(
    db: AsyncSession, data_dir: Path
) -> None:
    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})
    hashes = [f.sha256 for f in file_rows(source_id)]
    deleted = await source_service.delete(db, source_id, "wrong dataset", OPERATOR)
    assert deleted.state == "deleted"
    row = source_row(source_id)
    assert row.state == "deleted" and row.deleted_by == "Test Operator" and row.deleted_at
    assert row.deleted_by_origin == "operator"
    assert not (data_dir / "sources" / source_id).exists()
    assert [f.sha256 for f in file_rows(source_id)] == hashes


async def test_an_agent_may_delete_without_approval(db: AsyncSession, data_dir: Path) -> None:
    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})
    await source_service.delete(db, source_id, "cleanup", Who("agent:mcp", "agent"))
    assert source_row(source_id).deleted_by == "agent:mcp"


async def test_a_source_still_importing_is_refused(db: AsyncSession, clean_db: None) -> None:
    from src.core.database import sync_session_factory
    from tests.support import db_factories

    with sync_session_factory()() as s:
        source_id = db_factories.source(s, state="importing").id
        s.commit()
    with pytest.raises(AppError) as info:
        await source_service.delete(db, source_id, "x", OPERATOR)
    assert info.value.code == "source_importing"


async def test_a_failed_unlink_leaves_the_source_ready(
    db: AsyncSession, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})

    def refuse(_path: object) -> None:
        raise PermissionError("read-only volume")

    monkeypatch.setattr(source_service.shutil, "rmtree", refuse)
    with pytest.raises(AppError) as info:
        await source_service.delete(db, source_id, "x", OPERATOR)
    assert info.value.code == "source_delete_failed"
    assert source_row(source_id).state == "ready"
    assert (data_dir / "sources" / source_id).exists()


async def test_deleting_twice_is_harmless(db: AsyncSession, data_dir: Path) -> None:
    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})
    await source_service.delete(db, source_id, "x", OPERATOR)
    again = await source_service.delete(db, source_id, "x", OPERATOR)
    assert again.state == "deleted"


async def test_a_source_a_version_was_built_from_is_refused_naming_the_version(
    client: object, driver: object, data_dir: Path, operator_name: str
) -> None:
    from tests.integration.test_version_build import build, setup, src
    from tests.support.stub_operators import body

    source_id = make_source(data_dir, {"train": HUMOR_TRAIN})
    dataset, revision = await setup(client, body(("stub_drop_short", {"min_len": 8})))  # type: ignore[arg-type]
    version = await build(client, driver, dataset, revision, [src(source_id)], seed=1)  # type: ignore[arg-type]
    async with async_session_factory()() as session:
        with pytest.raises(AppError) as info:
            await source_service.delete(session, source_id, "x", OPERATOR)
    assert info.value.code == "source_in_use"
    [reader] = info.value.details["versions"]
    assert reader["version_id"] == version["id"] and reader["number"] == 1
    assert source_row(source_id).state == "ready"
    assert (data_dir / "sources" / source_id).exists()
