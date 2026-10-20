"""The build pipeline's harder paths (tasks 6.8, 8.2, 8.6, 8.7, 8.9–8.11, 9.2, 9.4, 9.6–9.11)."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, text, update

from src.core.clock import utc_now
from src.core.database import Base, get_sync_engine, sync_session_factory
from src.models import AgentLabelRow, Job, RowEvent, StepExecution, Version, VersionStep
from src.workers import janitor, version_build_tasks
from tests.integration.test_version_build import build, request, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TEST, HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]
R = "/api/v1/recipes"


def job_of(job_id: str) -> Job:
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        db.expunge(job)
        return job


def count(model: type) -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(model)) or 0)


async def revision(client: httpx.AsyncClient, name: str, recipe_body: dict[str, Any]) -> str:
    response = await client.post(R, json={"name": name, "body": recipe_body})
    assert response.status_code == 201, response.text
    return str(response.json()["head_revision_id"])


def steps_of(version_id: str) -> list[tuple[int, bool]]:
    with sync_session_factory()() as db:
        rows = db.execute(
            select(VersionStep.step_index, VersionStep.reused)
            .where(VersionStep.version_id == version_id)
            .order_by(VersionStep.step_index)
        ).all()
    return [(int(i), bool(r)) for i, r in rows]


class TestReuse:
    async def test_changing_step_3_of_4_reuses_steps_1_and_2(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        first = (
            ("stub_drop_short", {"min_len": 8}),
            ("stub_meta_tag", {}),
            ("stub_upper", {}),
            ("stub_keep", {}),
        )
        ds, rev = await setup(client, body(*first))
        v1 = await build(client, driver, ds, rev, [src(source)], seed=11)
        executions = count(StepExecution)
        changed = (first[0], first[1], ("stub_drop_short", {"min_len": 12}), first[3])
        rev2 = await revision(client, "changed", body(*changed))
        v2 = await build(client, driver, ds, rev2, [src(source)], seed=11)
        assert steps_of(v2["id"]) == [(0, True), (1, True), (2, True), (3, False), (4, False)]
        assert count(StepExecution) == executions + 2
        assert v2["drop_summary"][0]["reused"] is True
        assert v2["drop_summary"][0]["reasons"][0]["reason_code"] == "too_short"
        assert v1["drop_summary"][0]["reasons"] == v2["drop_summary"][0]["reasons"]

    async def test_a_new_label_run_recomputes_the_step_that_binds_it(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import bindings

        async def resolver(db: Any, run_id: str) -> dict[str, Any]:
            return {"complete": True}

        monkeypatch.setitem(bindings.BINDING_RESOLVERS, "label_run", resolver)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(
            client, body(("stub_drop_short", {"min_len": 8}), ("stub_labeler", {}))
        )
        await build(
            client,
            driver,
            ds,
            rev,
            [src(source)],
            seed=5,
            bindings=[{"kind": "label_run", "id": "run_a"}],
        )
        v2 = await build(
            client,
            driver,
            ds,
            rev,
            [src(source)],
            seed=5,
            bindings=[{"kind": "label_run", "id": "run_b"}],
        )
        assert steps_of(v2["id"]) == [(0, True), (1, True), (2, False)]

    async def test_two_builds_sharing_a_prefix_compute_it_once(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev_a = await setup(
            client, body(("stub_drop_short", {"min_len": 8}), ("stub_keep", {}))
        )
        rev_b = await revision(
            client, "b", body(("stub_drop_short", {"min_len": 8}), ("stub_upper", {}))
        )
        job_a = (await request(client, ds, rev_a, [src(source)], seed=9)).json()["job_id"]
        job_b = (await request(client, ds, rev_b, [src(source)], seed=9)).json()["job_id"]
        version_build_tasks.run_pass(job_a)  # assemble, dispatch step 1 (A owns it)
        version_build_tasks.run_pass(job_b)  # assemble reused, step 1 owned by A: wait
        with sync_session_factory()() as db:
            waiting = db.execute(
                text("SELECT waiting_execution_id FROM dw_version_builds WHERE job_id = :j"),
                {"j": job_b},
            ).scalar_one()
        assert waiting is not None
        assert driver.run(job_a) == "completed"
        assert ("midataworks.versions.advance_build", [job_b]) in driver.sent or driver.status(
            job_b
        ) != "queued"
        assert driver.run(job_b) == "completed"
        dispatched = [s.operator for s in driver.stubs.dispatched]
        assert dispatched.count("stub_drop_short") == 1


class TestLabelsFollowKeys:
    async def test_an_upstream_no_change_step_costs_no_endpoint_call(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_labeler", {})))
        await build(client, driver, ds, rev, [src(source)], seed=1)
        calls = driver.stubs.endpoint_calls
        assert calls == len({r["text"].strip() for r in HUMOR_TRAIN})  # one per key, not per copy
        rev2 = await revision(client, "with-keep", body(("stub_keep", {}), ("stub_labeler", {})))
        v2 = await build(client, driver, ds, rev2, [src(source)], seed=1)
        assert driver.stubs.endpoint_calls == calls, "labels resolved by key: zero endpoint calls"
        import pyarrow.parquet as pq

        rows = pq.read_table(data_dir / v2["splits"][0]["path"]).to_pylist()
        assert all(r["label_humor"] is not None for r in rows)

    async def test_a_step_that_changes_rows_leaves_only_those_unlabelled(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_labeler", {})))
        await build(client, driver, ds, rev, [src(source)], seed=1)
        calls = driver.stubs.endpoint_calls
        rev2 = await revision(client, "upper", body(("stub_upper", {}), ("stub_labeler", {})))
        await build(client, driver, ds, rev2, [src(source)], seed=1)
        changed_keys = len({r["text"].strip().upper() for r in HUMOR_TRAIN})
        assert driver.stubs.endpoint_calls == calls + changed_keys


class TestCancelResume:
    async def test_cancel_during_a_step_keeps_completed_steps_and_resumes(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8}), ("stub_keep", {})))
        job_id = (await request(client, ds, rev, [src(source)], seed=4)).json()["job_id"]
        version_build_tasks.run_pass(job_id)  # assemble done, step 1 dispatched
        cancelled = await client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "stop"})
        assert cancelled.status_code == 202
        driver.stubs.run_pending()  # 003 finishes anyway and calls the link
        version_build_tasks.run_pass(job_id)
        assert driver.status(job_id) == "cancelled"
        assert count(Version) == 0
        assert (
            not list((data_dir / "versions").glob("*"))
            if (data_dir / "versions").exists()
            else True
        )
        with sync_session_factory()() as db:
            states = sorted(db.execute(select(StepExecution.kind, StepExecution.state)).all())
        assert states == [("assemble", "completed"), ("operator", "cancelled")]
        again = (await request(client, ds, rev, [src(source)], seed=4)).json()["job_id"]
        assert driver.run(again) == "completed"
        version_id = job_of(again).result["version_id"]  # type: ignore[index]
        assert steps_of(version_id)[0] == (0, True)


class TestEvents:
    async def test_progress_and_completion_events_carry_their_payloads(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8})))
        v = await build(client, driver, ds, rev, [src(source)], seed=2)
        rooms = {room for room, _, _ in driver.emitted}
        assert rooms == {f"dataworks/version-builds/{v['build_job_id']}"}
        phases = [
            data["phase"] for _, event, data in driver.emitted if event == "version_build:progress"
        ]
        for phase in ("verify_sources", "assemble", "step", "ingest", "finalize"):
            assert phase in phases, phase
        step_events = [
            d
            for _, e, d in driver.emitted
            if e == "version_build:progress" and d["phase"] == "step"
        ]
        assert {"step_index", "step_count", "operator", "reused"} <= set(step_events[-1])
        completed = [d for _, e, d in driver.emitted if e == "version_build:completed"]
        assert completed == [
            {"job_id": v["build_job_id"], "version_id": v["id"], "dataset_id": ds, "number": 1}
        ]

    async def test_a_failure_emits_one_failed_event_with_the_envelope(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_bad_drop", {})))
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        driver.run(job_id)
        failed = [d for _, e, d in driver.emitted if e == "version_build:failed"]
        assert len(failed) == 1 and failed[0]["error"]["code"] == "unreasoned_event"


class TestIngestAtomicity:
    async def test_a_failure_mid_copy_leaves_no_events_and_no_completed_step(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import step_ingest

        real = step_ingest.copy_events

        def breaking(session: Any, execution_id: str, events: Path) -> int:
            real(session, execution_id, events)
            raise RuntimeError("disk full mid-copy")

        monkeypatch.setattr(step_ingest, "copy_events", breaking)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8})))
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        assert driver.run(job_id) == "failed"
        assert count(RowEvent) == 0
        with sync_session_factory()() as db:
            operator_state = db.execute(
                select(StepExecution.state).where(StepExecution.kind == "operator")
            ).scalar_one()
        assert operator_state != "completed"


class TestJanitor:
    async def test_a_build_waiting_on_a_live_step_is_not_reaped_and_a_dead_one_is(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        version_build_tasks.run_pass(job_id)  # dispatches step 1; the build's task returns
        old = utc_now() - timedelta(hours=2)
        with get_sync_engine().begin() as conn:
            conn.execute(
                update(Job).where(Job.id == job_id).values(heartbeat_at=old, started_at=old)
            )
        with sync_session_factory()() as db:
            assert janitor.sweep(db, active_ids=set()) == []
        with get_sync_engine().begin() as conn:
            conn.execute(update(StepExecution).values(heartbeat_at=old))
        with sync_session_factory()() as db:
            reaped = janitor.sweep(db, active_ids=set())
        assert [r.job_id for r in reaped] == [job_id]


class TestFinalize:
    async def test_split_roles_and_held_out_origin_are_inherited(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_split", {"fraction": 0.3})))
        v1 = await build(client, driver, ds, rev, [src(source)], seed=3)
        held = {s["name"]: s["held_out"] for s in v1["splits"]}
        assert held == {"train": False, "test": True}
        assert v1["held_out_origin_version_id"] == v1["id"]
        rev2 = await revision(client, "keep", body(("stub_keep", {})))
        v2 = await build(
            client, driver, ds, rev2, [{"kind": "version", "version_id": v1["id"]}], seed=3
        )
        assert v2["held_out_origin_version_id"] == v1["id"]

    async def test_no_duplicate_warning_when_copies_agree(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        rows = HUMOR_TEST + [dict(HUMOR_TEST[0])]
        source = make_source(data_dir, {"test": rows})
        ds, rev = await setup(client, body(("stub_keep", {})))
        v = await build(client, driver, ds, rev, [src(source)])
        assert v["warnings"] == []

    async def test_a_failed_commit_leaves_an_orphan_the_sweeper_removes(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import version_finalize, version_sweeper

        monkeypatch.setattr(version_finalize, "bytes_sha256", lambda content: "0" * 64)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        assert driver.run(job_id) == "failed"
        assert count(Version) == 0
        orphans = list((data_dir / "versions").iterdir())
        assert len(orphans) == 1 and (orphans[0] / "manifest.json").is_file()
        assert not [s for s in driver.sent if s[0] == "midataworks.curation.post_version"]
        with sync_session_factory()() as db:
            assert version_sweeper.sweep_orphans(db) == []  # too young
            assert version_sweeper.sweep_orphans(db, now=orphans[0].stat().st_mtime + 7200) == [
                orphans[0].name
            ]
        assert not orphans[0].exists()

    async def test_a_failed_rename_leaves_no_row_and_no_files(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import version_finalize

        def no_rename(src_path: Any, dst: Any) -> None:
            raise OSError("cross-device link")

        monkeypatch.setattr(version_finalize.os, "rename", no_rename)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
        assert driver.run(job_id) == "failed"
        assert count(Version) == 0
        assert (
            not list((data_dir / "versions").glob("*"))
            if (data_dir / "versions").exists()
            else True
        )
        assert not list((data_dir / "staging").glob("version-*"))

    async def test_concurrent_finalizes_on_one_dataset_get_consecutive_numbers(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        jobs = [
            (await request(client, ds, rev, [src(source)], seed=s)).json()["job_id"] for s in (1, 2)
        ]
        for job_id in jobs:
            version_build_tasks.run_pass(job_id)
        driver.stubs.run_pending()
        threads = [threading.Thread(target=version_build_tasks.run_pass, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with sync_session_factory()() as db:
            numbers = sorted(db.execute(select(Version.number)).scalars())
        assert numbers == [1, 2]

    async def test_post_version_is_enqueued_once_after_commit(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        v = await build(client, driver, ds, rev, [src(source)])
        enqueued = [s for s in driver.sent if s[0] == "midataworks.curation.post_version"]
        assert enqueued == [("midataworks.curation.post_version", [v["id"]])]

    async def test_a_broker_error_on_post_version_leaves_the_version_committed(
        self,
        client: httpx.AsyncClient,
        driver: BuildDriver,
        data_dir: Path,
        operator_name: str,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def broken(name: str, args: list[Any] | None = None, **_: Any) -> None:
            if name == "midataworks.curation.post_version":
                raise ConnectionError("broker down")
            driver.send_task(name, args)

        monkeypatch.setattr(version_build_tasks, "_send_task", broken)
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_keep", {})))
        v = await build(client, driver, ds, rev, [src(source)])
        assert v["state"] == "completed"
        assert "post_version_enqueue_failed" in caplog.text


class TestRowsOnlyInParquet:
    async def test_no_table_holds_row_text_after_a_build(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        source = make_source(data_dir, {"train": HUMOR_TRAIN})
        ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8}), ("stub_upper", {})))
        await build(client, driver, ds, rev, [src(source)])
        chunks: list[str] = []
        with get_sync_engine().connect() as conn:
            for table in Base.metadata.sorted_tables:
                chunks.extend(
                    conn.execute(text(f"SELECT t::text FROM {table.name} AS t")).scalars()
                )
        everything = "\n".join(chunks)
        for row in (r for r in HUMOR_TRAIN if len(r["text"]) >= 12):
            assert row["text"] not in everything
            assert row["text"].upper() not in everything


class TestLabelRowGate:
    """FR-002.50 (S3-02): an agent build that labels rows counts toward P-07's window."""

    async def _setup(
        self, client: httpx.AsyncClient, data_dir: Path, rows: int, recipe_body: dict[str, Any]
    ) -> tuple[str, str, str]:
        many = [
            {
                "text": f"row number {i} of the gate fixture",
                "label": i % 2,
                "score": 0.1,
                "note": None,
            }
            for i in range(rows)
        ]
        source = make_source(data_dir, {"train": many})
        ds, rev = await setup(client, recipe_body)
        return ds, rev, source

    async def test_an_agent_build_over_the_threshold_waits_for_approval(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        ds, rev, source = await self._setup(
            client, data_dir, 6000, body(("stub_probe_verdict", {}))
        )
        response = await request(client, ds, rev, [src(source)], seed=1)
        response = await client.post(
            "/api/v1/versions",
            json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)], "seed": 2},
            headers={"X-Dataworks-Agent": "agent:mcp"},
        )
        assert response.status_code == 202 and "approval_id" in response.json()
        assert response.json()["action"] == "agent_label_rows"
        assert count(AgentLabelRow) == 0
        approved = await client.post(f"/api/v1/approvals/{response.json()['approval_id']}/approve")
        assert approved.json()["status"] == "executed", approved.text
        with sync_session_factory()() as db:
            rows = list(db.execute(select(AgentLabelRow)).scalars())
        assert [(r.run_kind, r.rows_counted, r.identity) for r in rows] == [
            ("probe_verdict", 6000, "agent:mcp")
        ]

    async def test_an_agent_recipe_build_over_the_threshold_waits_for_approval(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        """The alias route carries the same gate (010 ledger: exempt as an alias BECAUSE of it)."""
        ds, rev, source = await self._setup(
            client, data_dir, 6000, body(("stub_probe_verdict", {}))
        )
        recipe_id = (await client.get(R)).json()["items"][0]["id"]
        response = await client.post(
            f"{R}/{recipe_id}/build",
            json={"dataset_id": ds, "inputs": [src(source)], "seed": 3},
            headers={"X-Dataworks-Agent": "agent:mcp"},
        )
        assert response.status_code == 202 and "approval_id" in response.json(), response.text
        assert response.json()["action"] == "agent_label_rows"
        assert count(AgentLabelRow) == 0

    async def test_an_operator_build_is_never_gated(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        ds, rev, source = await self._setup(
            client, data_dir, 6000, body(("stub_probe_verdict", {}))
        )
        response = await request(client, ds, rev, [src(source)], seed=1)
        assert response.status_code == 202 and "job_id" in response.json()
        assert count(AgentLabelRow) == 0

    async def test_a_recipe_with_no_scoring_step_is_never_gated(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        ds, rev, source = await self._setup(client, data_dir, 6000, body(("stub_keep", {})))
        response = await client.post(
            "/api/v1/versions",
            json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]},
            headers={"X-Dataworks-Agent": "agent:mcp"},
        )
        assert response.status_code == 202 and "job_id" in response.json()

    async def test_an_agent_build_under_the_threshold_runs_and_is_recorded(
        self, client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
    ) -> None:
        ds, rev, source = await self._setup(client, data_dir, 40, body(("stub_probe_verdict", {})))
        recipe_id = (await client.get(R)).json()["items"][0]["id"]
        response = await client.post(
            f"{R}/{recipe_id}/build",
            json={"dataset_id": ds, "inputs": [src(source)]},
            headers={"X-Dataworks-Agent": "agent:mcp"},
        )
        assert response.status_code == 202 and "job_id" in response.json(), response.text
        with sync_session_factory()() as db:
            rows = list(db.execute(select(AgentLabelRow)).scalars())
        assert [(r.run_kind, r.rows_counted, r.version_id) for r in rows] == [
            ("probe_verdict", 40, source)
        ]
        assert uuid.UUID(rows[0].version_id)


async def test_logs_carry_row_keys_never_row_text(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    data_dir: Path,
    operator_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Task 18.6: a build's log lines name keys and counts; no row's text reaches a log."""
    import logging

    caplog.set_level(logging.DEBUG)
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_bad_drop", {})))
    job_id = (await request(client, ds, rev, [src(source)])).json()["job_id"]
    driver.run(job_id)
    ds2, rev2 = await setup(client, body(("stub_drop_short", {"min_len": 8})), name="ok")
    await build(client, driver, ds2, rev2, [src(source)])
    logged = caplog.text
    assert "ingested step execution" in logged  # the guard sees real log lines
    for row in (r for r in HUMOR_TRAIN if len(r["text"]) >= 12):
        assert row["text"] not in logged


def test_the_secret_scan_covers_every_002_table() -> None:
    """Task 18.6: Foundation's scan reads Base.metadata, so the new tables are in it."""
    names = {t.name for t in Base.metadata.sorted_tables}
    assert {"dw_versions", "dw_recipe_bodies", "dw_row_events", "dw_sources"} <= names
