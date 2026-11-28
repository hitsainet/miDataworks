"""Seed origin, chunk integrity, identity and the append-only triggers (007 FTASKS 2.6, 15.6,
15.10; controls C17, C25, C33)."""

from __future__ import annotations

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from tests.support.generation_fixtures import (
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)

RUNS = "/api/v1/generation-runs"


async def completed(client: httpx.AsyncClient, gen: Gen, version: str, n: int) -> str:
    run_id = (
        await client.post(
            RUNS, json=run_body(version, await respond_template(client), sample_size=n)
        )
    ).json()["id"]
    assert gen.run_until_done(run_id).state == "completed"
    return str(run_id)


async def test_generated_rows_are_never_seeds(client: httpx.AsyncClient, gen: Gen) -> None:
    version = make_version(
        version_table(["source one", "source two"], generated=[f"GENERATED {i}" for i in range(20)])
    )
    await completed(client, gen, version, 22)
    prompts = [c.body["messages"][-1]["content"] for c in gen.chats()]
    assert sorted(prompts) == ["source one", "source two"]


async def test_a_changed_committed_chunk_is_refused_by_the_reader(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    from src.operators.errors import OperatorError
    from src.operators.native.generation import committed_manifest, committed_records

    run_id = await completed(client, gen, make_version(version_table(["one", "two"])), 2)
    manifest = committed_manifest(run_id)
    # 007 mutation round (2026-10-17), survivor C13: the manifest records each chunk's real hash,
    # so an untouched run reads back (a wrong hash refuses every reader, the tamper test alone
    # could not tell)
    assert len(committed_records(manifest, "respond")) == 2
    path = resolve_under_data_dir(manifest["chunks"][0]["path"])
    table = pq.read_table(path)
    pq.write_table(table.slice(0, 1), path)  # a committed file altered afterwards
    with pytest.raises(OperatorError) as refused:
        committed_records(manifest, "respond")
    assert refused.value.code == "generation_chunk_changed"


async def test_a_blank_identity_is_refused(client: httpx.AsyncClient, gen: Gen) -> None:
    from src.core.agent_origin import Who
    from src.core.database import get_db
    from src.core.errors import AppError
    from src.services.generation import run_service

    async for db in get_db():
        with pytest.raises(AppError) as refused:
            await run_service.start(db, None, None, Who("  ", "operator"))  # type: ignore[arg-type]
        assert refused.value.code == "NO_IDENTITY"
        break
    with sync_session_factory()() as db:
        db.execute(text("DELETE FROM dw_app_settings WHERE key = 'operator_name'"))
        db.commit()
    version = make_version(version_table(["one"]))
    response = await client.post(RUNS, json=run_body(version, await respond_template(client)))
    assert response.status_code == 422 and response.json()["error"]["code"] == "NO_IDENTITY"


@pytest.mark.parametrize(
    "table",
    [
        "dw_steering_snapshots",
        "dw_generation_records",
        "dw_generation_pairs",
        "dw_diversity_reports",
    ],
)
async def test_append_only_tables_refuse_update_and_delete(
    client: httpx.AsyncClient, gen: Gen, table: str
) -> None:
    from tests.integration.generation.test_steered_run import PROFILE, dpo_version, steered_body

    gen.fake.profiles = {"prof-1": dict(PROFILE)}
    version = dpo_version(["one", "two"])
    run_id = (
        await client.post(
            RUNS, json=steered_body(version, await respond_template(client), sample_size=2)
        )
    ).json()["id"]
    assert gen.run_until_done(run_id).state == "completed"
    with sync_session_factory()() as db:
        db.execute(
            text(
                'INSERT INTO dw_diversity_reports (id, version_id, "column", method_hash, method, splits, '
                "sample_size, seed, clustering, figures, checks, verdict, created_by, created_by_origin) "
                "VALUES ('dr_t', :v, 'c', :h, '{}', '[]', 1, 1, '{}', '{}', '[]', 'holds', 'x', 'operator')"
            ),
            {"v": version, "h": "0" * 64},
        )
        db.commit()
    for statement in (
        (
            f"UPDATE {table} SET run_id = run_id"
            if table != "dw_diversity_reports"
            else "UPDATE dw_diversity_reports SET verdict = 'falls'"
        ),
        f"DELETE FROM {table}",
    ):
        with sync_session_factory()() as db, pytest.raises(DBAPIError):
            assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() > 0  # noqa: S608
            db.execute(text(statement))
            db.commit()


async def test_committing_a_chunk_twice_writes_nothing_the_second_time(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """007 mutation round (2026-10-17), survivor C14: a retried chunk commit (a worker restarted
    after the marker landed) finds the marker and writes nothing: no duplicate records, no error."""
    from sqlalchemy import func, select

    from src.models.generation import GenerationChunk, GenerationRecord
    from src.services.generation import record_store

    run_id = await completed(client, gen, make_version(version_table(["one", "two"])), 2)
    with sync_session_factory()() as db:
        chunk = db.execute(
            select(GenerationChunk).where(GenerationChunk.run_id == run_id).limit(1)
        ).scalar_one()
        before = db.execute(
            select(func.count())
            .select_from(GenerationRecord)
            .where(GenerationRecord.run_id == run_id)
        ).scalar_one()
        again = record_store.commit_chunk(
            db,
            run_id,
            chunk.stage,
            chunk.chunk_index,
            chunk.job_id,
            resolve_under_data_dir(chunk.path),
            chunk.file_sha256,
            None,
            None,
        )
        after = db.execute(
            select(func.count())
            .select_from(GenerationRecord)
            .where(GenerationRecord.run_id == run_id)
        ).scalar_one()
    assert again is False and after == before > 0
