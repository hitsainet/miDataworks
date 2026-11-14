"""A check over a whole version and a step's refusal read from a preview (ENH-006 D116 G8).

The tool is called through the live MCP registry over the real backend; the job it starts is taken
from the broker call the route makes and run through the task that call names, so the route, the
job kind, the task and the operator are all the real ones.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import select

from src.api.v1.endpoints import operators as endpoint
from src.core.celery_app import celery_app, route_for
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.job import Job
from src.models.row_event import RowEvent
from src.models.version import Version
from src.operators import preview
from src.operators.errors import STATUS_FOR_CODE, StepFailed, stored_refusal
from src.operators.manifest import OperatorManifest, ResourceSpec, manifest_hash
from src.operators.native.behaviour.check_carrier_ids import CheckCarrierIds
from src.operators.registry import current
from src.workers import operator_tasks
from tests.support.gap_fixtures import split_version
from tests.support.mcp_live import call, live_mcp, refusal_of

ROLES = {"generation_run_id": "metadata", "genre": "metadata", "carrier_id": "metadata"}


def _row(i: int, carrier: str, run: str, genre: str) -> dict[str, Any]:
    return {
        "text": f"carrier text {i}",
        "carrier_id": carrier,
        "generation_run_id": run,
        "genre": genre,
    }


def colliding_rows(ids: int, clean: int, start: int = 0) -> list[dict[str, Any]]:
    """Each of ``ids`` carrier ids held by two runs in different genres, then ``clean`` own ids."""
    rows: list[dict[str, Any]] = []
    for i in range(ids):
        rows.append(_row(start + 2 * i, f"c{i}", "gr_a", "g1"))
        rows.append(_row(start + 2 * i + 1, f"c{i}", "gr_b", "g2"))
    rows.extend(_row(start + 2 * ids + i, f"own{i}", "gr_a", "g1") for i in range(clean))
    return rows


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[Any]]]:
    """Every task the route hands the broker: the wiring, recorded instead of a network call."""
    calls: list[tuple[str, list[Any]]] = []

    def record(name: str, args: list[Any] | None = None, **options: Any) -> None:
        calls.append((name, list(args or [])))

    monkeypatch.setattr(celery_app, "send_task", record)
    return calls


async def _start(arguments: dict[str, Any]) -> dict[str, Any]:
    mcp, mcp_client = live_mcp()
    try:
        answer: dict[str, Any] = await call(mcp, "dataworks_run_version_check", arguments)
        return answer
    finally:
        await mcp_client.close()


def _job(job_id: str) -> Job:
    with sync_session_factory()() as db:
        row = db.get(Job, job_id)
        assert row is not None
        db.expunge(row)
        return row


async def _run(arguments: dict[str, Any], sent: list[tuple[str, list[Any]]]) -> Job:
    """Start the check through the tool, then run exactly what the route sent to the broker."""
    before = len(sent)
    started = await _start(arguments)
    new = sent[before:]
    assert new == [("midataworks.operators.run_version_check", [started["job_id"]])]
    celery_app.loader.import_default_modules()
    name, args = new[0]
    assert route_for(name) == "curation"
    celery_app.tasks[name](*args)
    return _job(started["job_id"])


# --- the check ---------------------------------------------------------------------------------


async def test_the_real_reference_refuses_with_every_id_and_row(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    """Version 28 of the dry run: 2,653 ids on 5,306 rows, among others. 5,306 is past the 2,000 rows
    a preview may read, and the ids sit on both splits, so one split alone holds only part of them.
    """
    rows = colliding_rows(2653, 400)
    version = split_version(data_dir, {"train": rows[::2], "test": rows[1::2]}, roles=ROLES)
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "completed", job.error
    assert job.result is not None
    assert job.result["verdict"] == "refused"
    refusal = job.result["refusal"]
    assert refusal["code"] == "carrier_id_collision"
    assert refusal["details"]["ids"] == 2653 and refusal["details"]["rows"] == 5306
    assert refusal["details"]["runs"] == ["gr_a", "gr_b"]
    assert job.result["rows_in_scope"] == 5706 and job.result["split"] is None
    assert job.result["operator"] == "check_carrier_ids@1.0.0"


async def test_a_preview_of_the_same_rows_can_pass_where_the_whole_version_refuses(
    client: httpx.AsyncClient,
    data_dir: Path,
    sent: list[tuple[str, list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One collision among 3,000 rows: the sample's pass is why this tool exists."""
    monkeypatch.setattr(endpoint, "send_preview", lambda req, entry: preview.run_request(req))
    rows = colliding_rows(0, 3000)
    rows[10]["carrier_id"] = rows[2990]["carrier_id"] = "shared"
    rows[10]["generation_run_id"], rows[10]["genre"] = "gr_b", "g2"
    version = split_version(data_dir, {"train": rows}, roles=ROLES)
    shown = await client.post(
        "/api/v1/operators/check_carrier_ids/1.0.0/preview",
        json={"input": {"version_id": version}, "params": {}, "sample_size": 50, "seed": 3},
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["result"]["next"]["tool"] == "dataworks_run_version_check"
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["details"]["ids"] == 1


async def test_clean_rows_pass_counting_every_row_of_every_split_and_writing_nothing(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    rows = colliding_rows(0, 2500)
    train = [{**r, "only_in_train": "x"} for r in rows[:2000]]
    test = [{**r, "only_in_train": None} for r in rows[2000:]]
    version = split_version(data_dir, {"train": train, "test": test}, roles=ROLES)
    files_before = sorted(p for p in data_dir.rglob("*") if p.is_file())
    with sync_session_factory()() as db:
        versions_before = db.execute(select(Version.id)).scalars().all()
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "completed", job.error
    assert job.result is not None
    assert job.result["verdict"] == "pass"
    assert job.result["rows_checked"] == 2500 == job.result["rows_in_scope"]
    assert job.result["reports"] == [{"checked_rows": 2500}]
    assert sorted(p for p in data_dir.rglob("*") if p.is_file()) == files_before
    with sync_session_factory()() as db:
        assert db.execute(select(Version.id)).scalars().all() == versions_before
        assert db.execute(select(RowEvent)).first() is None


async def test_splits_that_differ_by_a_column_read_as_one_input(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    """An id can cross splits whose files do not share a shape: read by name, not by position."""
    rows = colliding_rows(2, 2)
    train = [{"only_in_train": "x", **r} for r in rows[::2]]
    test = [dict(reversed(list(r.items()))) for r in rows[1::2]]
    version = split_version(data_dir, {"train": train, "test": test}, roles=ROLES)
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "completed", job.error
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["details"]["ids"] == 2


async def test_one_split_reads_only_that_split(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    rows = colliding_rows(5, 0)
    version = split_version(
        data_dir,
        {"train": colliding_rows(0, 40, start=100), "test": rows},
        roles=ROLES,
    )
    train = await _run({"version_id": version, "split": "train", "name": "check_carrier_ids"}, sent)
    assert train.result is not None
    assert train.result["verdict"] == "pass" and train.result["rows_checked"] == 40
    assert train.result["split"] == "train" and train.result["rows_in_scope"] == 40
    test = await _run({"version_id": version, "split": "test", "name": "check_carrier_ids"}, sent)
    assert test.result is not None and test.result["verdict"] == "refused"
    assert test.result["refusal"]["details"]["rows"] == 10


async def test_it_never_passes_because_a_column_is_gone(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": [{"text": f"t{i}"} for i in range(5)]})
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "completed"
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["code"] == "carrier_columns_missing"


async def test_only_a_manifest_that_says_check_may_run(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": [{"text": "only row"}]})
    with pytest.raises(ToolError) as exc:
        await _start({"version_id": version, "name": "drop_columns", "params": {"columns": ["x"]}})
    code, status, details = refusal_of(exc.value)
    assert (code, status) == ("operator_not_a_check", 409)
    assert details["next"]["tool"] == "dataworks_list_operators"
    assert sent == []


async def test_bad_arguments_are_refused_before_a_job_exists(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": [{"text": "only row"}]})
    for arguments, status, code in (
        ({"name": "no_such_operator"}, 404, "operator_not_found"),
        ({"name": "check_carrier_ids", "split": "nope"}, 404, "split_not_found"),
        ({"name": "check_carrier_ids", "params": {"x": 1}}, 422, "params_invalid"),
    ):
        with pytest.raises(ToolError) as exc:
            await _start({"version_id": version, **arguments})
        assert refusal_of(exc.value)[:2] == (code, status)
    with sync_session_factory()() as db:
        assert db.execute(select(Job).where(Job.kind == "version_check")).first() is None
    assert sent == []


async def test_a_crash_fails_the_job_and_is_never_a_pass(
    client: httpx.AsyncClient,
    data_dir: Path,
    sent: list[tuple[str, list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(self: Any, batch: Any, params: Any, ctx: Any) -> None:
        raise RuntimeError("the operator crashed")

    monkeypatch.setattr(CheckCarrierIds, "run", boom)
    version = split_version(data_dir, {"train": colliding_rows(0, 5)}, roles=ROLES)
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "failed"
    assert "the operator crashed" in (job.error or "")
    assert not (job.result or {}).get("verdict")


async def test_a_check_that_changes_a_row_is_refused(
    client: httpx.AsyncClient,
    data_dir: Path,
    sent: list[tuple[str, list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.operators.native.curation.common import read_all
    from src.operators.protocol import OperatorResult

    def adds_a_column(self: Any, batch: Any, params: Any, ctx: Any) -> OperatorResult:
        table = read_all(ctx)
        return OperatorResult(
            output=table.append_column("added", table.column("text")), report=None
        )

    monkeypatch.setattr(CheckCarrierIds, "run", adds_a_column)
    version = split_version(data_dir, {"train": colliding_rows(0, 5)}, roles=ROLES)
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.status == "completed"
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["code"] == "check_changed_rows"


async def test_reading_fewer_rows_than_the_version_records_is_not_a_pass(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    from sqlalchemy import text as sql

    version = split_version(data_dir, {"train": colliding_rows(0, 5)}, roles=ROLES)
    with sync_session_factory()() as db:
        row = db.get(Version, version)
        assert row is not None
        splits = [{**row.splits[0], "rows": 6}]
        db.execute(sql("ALTER TABLE dw_versions DISABLE TRIGGER USER"))
        db.execute(
            sql("UPDATE dw_versions SET splits = CAST(:s AS jsonb) WHERE id = :v"),
            {"s": json.dumps(splits), "v": version},
        )
        db.execute(sql("ALTER TABLE dw_versions ENABLE TRIGGER USER"))
        db.commit()
    job = await _run({"version_id": version, "name": "check_carrier_ids"}, sent)
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["code"] == "check_read_too_few_rows"


def _queued_job(version: str, operator: str, operator_version: str, params: dict[str, Any]) -> str:
    """A job row as the route writes it, for what the worker must decide for itself."""
    with sync_session_factory()() as db:
        job = Job(
            id=new_id("job"),
            kind="version_check",
            status="queued",
            progress=0.0,
            params={
                "version_id": version,
                "split": None,
                "operator": operator,
                "operator_version": operator_version,
                "operator_params": params,
            },
            started_by="Test Operator",
            started_by_origin="operator",
        )
        db.add(job)
        db.commit()
        return job.id


async def test_the_worker_decides_again_what_is_a_check(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    """A job queued before an upgrade may name an operator that no longer says it is one."""
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    job_id = _queued_job(version, "drop_columns", "1", {"columns": ["genre"]})
    operator_tasks.run_version_check(job_id)
    job = _job(job_id)
    assert job.status == "completed"
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["code"] == "operator_not_a_check"


async def test_an_operator_gone_since_the_job_was_queued_is_a_refusal(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    job_id = _queued_job(version, "check_carrier_ids", "9.9.9", {})
    operator_tasks.run_version_check(job_id)
    job = _job(job_id)
    assert job.status == "completed"
    assert job.result is not None and job.result["verdict"] == "refused"
    assert job.result["refusal"]["code"] == "version_unavailable"


async def test_the_worker_checks_the_params_again(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    job_id = _queued_job(version, "check_carrier_ids", "1.0.0", {"not_a_param": 1})
    operator_tasks.run_version_check(job_id)
    job = _job(job_id)
    assert job.result is not None and job.result["refusal"]["code"] == "params_invalid"


async def test_a_cancel_stops_it_and_stores_no_verdict(
    client: httpx.AsyncClient,
    data_dir: Path,
    sent: list[tuple[str, list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import text as sql

    from src.core import cancellation
    from src.core.database import get_sync_engine

    monkeypatch.setattr(cancellation, "DEFAULT_POLL_INTERVAL_S", 0.0)
    real = CheckCarrierIds.run
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    job_id = _queued_job(version, "check_carrier_ids", "1.0.0", {})

    def cancelled_first(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        with get_sync_engine().begin() as conn:
            conn.execute(
                sql(
                    "UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:id"
                ),
                {"id": job_id},
            )
        ctx.check_cancel()
        return real(self, batch, params, ctx)

    monkeypatch.setattr(CheckCarrierIds, "run", cancelled_first)
    operator_tasks.run_version_check(job_id)
    job = _job(job_id)
    assert job.status == "cancelled"
    assert not (job.result or {}).get("verdict")


async def test_a_long_check_beats_so_the_janitor_leaves_it_alone(
    client: httpx.AsyncClient,
    data_dir: Path,
    sent: list[tuple[str, list[Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One long compute reaches no checkpoint of its own: only the beat keeps the job's row fresh."""
    import time

    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.02)
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    job_id = _queued_job(version, "check_carrier_ids", "1.0.0", {})
    seen: list[Any] = []
    real = CheckCarrierIds.run

    def slow(self: Any, batch: Any, params: Any, ctx: Any) -> Any:
        seen.append(_job(job_id).heartbeat_at)
        time.sleep(0.4)
        seen.append(_job(job_id).heartbeat_at)
        return real(self, batch, params, ctx)

    monkeypatch.setattr(CheckCarrierIds, "run", slow)
    operator_tasks.run_version_check(job_id)
    assert seen[1] > seen[0], "the row's heartbeat moved during the operator's run"


async def test_finishing_starts_the_next_queued_job(
    client: httpx.AsyncClient, data_dir: Path, sent: list[tuple[str, list[Any]]]
) -> None:
    version = split_version(data_dir, {"train": colliding_rows(0, 4)}, roles=ROLES)
    first = _queued_job(version, "check_carrier_ids", "1.0.0", {})
    following = _queued_job(version, "check_carrier_ids", "1.0.0", {})
    operator_tasks.run_version_check(first)
    assert ("midataworks.operators.run_version_check", [following]) in sent


async def test_the_live_registry_marks_it_and_the_catalogue_says_so(
    client: httpx.AsyncClient,
) -> None:
    assert current().entry("check_carrier_ids", "1.0.0").manifest.check is True
    listed = (await client.get("/api/v1/operators", params={"limit": 200})).json()["items"]
    checks = {i["name"] for i in listed if i["check"]}
    assert "check_carrier_ids" in checks
    assert not checks & {"split", "drop_columns", "rule_label"}, "a check changes no row"
    shown = (await client.get("/api/v1/operators/check_carrier_ids/1.0.0")).json()
    assert shown["check"] is True


def test_the_field_moves_no_operators_hash() -> None:
    base: dict[str, Any] = {
        "name": "a_check",
        "version": "1",
        "provider": "native",
        "provider_version": "1",
        "kind": "mapper",
        "description": "x",
        "params_schema": {"type": "object", "properties": {}},
        "resources": ResourceSpec(queue="curation"),
        "deterministic": True,
    }
    marked = OperatorManifest(**base, check=True)
    assert marked.check is True
    assert manifest_hash(marked) == manifest_hash(OperatorManifest(**base))
    # A build's step reuse keys on this hash: marking an operator a check must not move it.
    assert (
        manifest_hash(current().entry("check_carrier_ids", "1.0.0").manifest)
        == current().entry("check_carrier_ids", "1.0.0").manifest_hash
    )


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "filter"},
        {"resources": ResourceSpec(queue="labeling", endpoint_role="judge")},
    ],
    ids=["not_a_mapper", "calls_a_model"],
)
def test_a_manifest_cannot_call_a_writer_a_check(change: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "name": "a_check",
        "version": "1",
        "provider": "native",
        "provider_version": "1",
        "kind": "mapper",
        "description": "x",
        "params_schema": {"type": "object", "properties": {}},
        "resources": ResourceSpec(queue="curation"),
        "deterministic": True,
        "check": True,
    }
    with pytest.raises(ValueError, match="a check is a native mapper"):
        OperatorManifest(**{**base, **change})


# --- a step's refusal, read from a preview ---------------------------------------------------


@pytest.fixture
def inline_previews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(endpoint, "send_preview", lambda req, entry: preview.run_request(req))


async def test_a_previews_refusal_reads_409_with_its_code_and_details_through_mcp(
    client: httpx.AsyncClient, data_dir: Path, inline_previews: None
) -> None:
    version = split_version(data_dir, {"train": colliding_rows(3, 4)}, roles=ROLES)
    mcp, mcp_client = live_mcp()
    try:
        with pytest.raises(ToolError) as exc:
            await call(
                mcp,
                "dataworks_preview_operator",
                {"name": "check_carrier_ids", "version": "1.0.0", "input": {"version_id": version}},
            )
    finally:
        await mcp_client.close()
    code, status, details = refusal_of(exc.value)
    assert (code, status) == ("carrier_id_collision", 409)
    assert details["ids"] == 3 and details["rows"] == 6
    assert details["next"]["tool"] == "dataworks_revise_recipe"


async def test_the_stored_answer_reads_the_same_refusal(
    client: httpx.AsyncClient, data_dir: Path, inline_previews: None
) -> None:
    version = split_version(data_dir, {"train": colliding_rows(1, 2)}, roles=ROLES)
    body = {"input": {"version_id": version}, "params": {}, "seed": 11}
    first = await client.post("/api/v1/operators/check_carrier_ids/1.0.0/preview", json=body)
    again = await client.post("/api/v1/operators/check_carrier_ids/1.0.0/preview", json=body)
    assert first.status_code == again.status_code == 409
    assert first.json()["error"] == again.json()["error"]
    assert again.json()["error"]["code"] == "carrier_id_collision"


@pytest.mark.parametrize(
    ("path", "params_of"),
    [
        ("preview", "version"),
        ("statistics", "version"),
        ("preview", "source"),
        ("statistics", "source"),
    ],
)
async def test_every_input_and_mode_names_the_refusal(
    client: httpx.AsyncClient,
    data_dir: Path,
    inline_previews: None,
    path: str,
    params_of: str,
) -> None:
    """``label_run_not_published`` has no row in the table: it read 500 on every one of these."""
    from tests.support.version_fixtures import HUMOR_TRAIN, make_source

    assert "label_run_not_published" not in STATUS_FOR_CODE
    source = (
        {"source_id": make_source(data_dir, {"train": HUMOR_TRAIN})}
        if params_of == "source"
        else {"version_id": split_version(data_dir, {"train": [{"text": "a row"}]})}
    )
    got = await client.post(
        f"/api/v1/operators/threshold_labeler/1/{path}",
        json={
            "params": {
                "label_run_id": "lr_never",
                "threshold_positive": 0.8,
                "threshold_negative": 0.2,
            },
            "input": source,
        },
    )
    assert got.status_code == 409, got.text
    error = got.json()["error"]
    assert error["code"] == "label_run_not_published"
    assert error["details"] == {"label_run_id": "lr_never"}


async def test_a_step_output_input_names_the_refusal(
    client: httpx.AsyncClient,
    data_dir: Path,
    inline_previews: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third input: a completed step's output, resolved through its own branch of the route."""
    version = split_version(data_dir, {"train": [{"text": "a row"}]})

    async def resolved(
        db: Any, body: Any, *, preview_id: str = "", size: int = 0
    ) -> dict[str, Any]:
        return {
            "files": [f"versions/{version}/train.parquet"],
            "column_roles": {"text": "content"},
            "rowkey_scheme": "dw.rowkey/v1",
        }

    monkeypatch.setattr(endpoint, "_resolve_input", resolved)
    got = await client.post(
        "/api/v1/operators/threshold_labeler/1/preview",
        json={
            "params": {
                "label_run_id": "lr_never",
                "threshold_positive": 0.8,
                "threshold_negative": 0.2,
            },
            "input": {"step_execution_id": "se_x"},
        },
    )
    assert got.status_code == 409
    assert got.json()["error"]["code"] == "label_run_not_published"


@pytest.mark.parametrize("worker", ["designer", "datajuicer"])
async def test_a_runner_shaped_preview_names_its_steps_refusal(
    client: httpx.AsyncClient,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    worker: str,
) -> None:
    """The runner images hand their answer back through the same task body."""

    def refuse(registry: Any, request: Any, raw: Any) -> None:
        raise StepFailed("generation_stage_only", "Only a generation stage runs this.", {"a": 1})

    monkeypatch.setattr(preview, f"run_{worker}", refuse)
    monkeypatch.setattr(
        endpoint,
        "send_preview",
        lambda req, entry: preview.run_request({**req, worker: True}, {"rows": []}),
    )
    version = split_version(data_dir, {"train": [{"text": "a row"}]})
    got = await client.post(
        "/api/v1/operators/fx_keep_all/1/preview",
        json={"params": {}, "input": {"version_id": version}, "seed": 21},
    )
    assert got.status_code == 409, got.text
    assert got.json()["error"] == {
        "code": "generation_stage_only",
        "message": "Only a generation stage runs this.",
        "details": {"a": 1},
    }


async def test_a_service_refusal_in_a_step_keeps_its_code(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.errors import ConflictError

    def refuse(registry: Any, request: Any) -> None:
        raise ConflictError("A service refused.", code="service_refusal", details={"b": 2})

    monkeypatch.setattr(preview, "run_preview", refuse)
    monkeypatch.setattr(endpoint, "send_preview", lambda req, entry: preview.run_request(req))
    version = split_version(data_dir, {"train": [{"text": "a row"}]})
    got = await client.post(
        "/api/v1/operators/fx_keep_all/1/preview",
        json={"params": {}, "input": {"version_id": version}, "seed": 22},
    )
    assert got.status_code == 409
    assert got.json()["error"]["code"] == "service_refusal"
    assert got.json()["error"]["details"] == {"b": 2}


def test_a_stored_timeout_and_a_lost_worker_are_not_called_refusals() -> None:
    timed_out = stored_refusal({"code": "preview_timeout", "message": "slow"})
    missing = stored_refusal({"code": "worker_unavailable", "message": "gone"})
    assert (timed_out.status_code, missing.status_code) == (504, 503)
    named = stored_refusal({"code": "a_new_refusal", "message": "no", "details": {"x": 1}})
    assert (named.status_code, named.details) == (409, {"x": 1})
