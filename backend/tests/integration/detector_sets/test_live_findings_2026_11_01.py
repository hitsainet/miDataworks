"""What the SSN pilot's set check found live, 2026-11-01 (ENH-006 D107, D108).

- A later build binds no label run itself, and D-7 read only a version's own bindings: an
  uncalibrated labeler in the lineage read green.
- The cross-role audit named its column ``role``, and a dataset's own ``role`` column made the check
  a 500.
- Numeric work on ``run_sync``'s greenlet segfaulted the API process: numpy walks the C stack for a
  chained temporary of 256 KiB or more. Each route that measures rows is asserted off the greenlet,
  with its payload and call count. The crash itself depends on the C library's unwinder and does not
  reproduce on every machine (on the node's image it does; on a developer's it did not), so a test
  that ran the path and looked for a crash could pass with the guard removed.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from greenlet import getcurrent, greenlet
from sqlalchemy import text

from src.core.database import (
    async_session_factory,
    off_greenlet,
    run_in_own_session,
    sync_session_factory,
)
from src.models import Version
from src.models.curation import VersionReport
from src.models.version import VersionInput
from src.services.curation import leakage_service
from src.services.curation.codes import InputFiles, ReportInput, cross_role_column, load_table
from src.services.detector_sets import curation_seam, length, send_service, set_service
from tests.support.calibration_fixtures import make_run
from tests.support.detector_fixtures import (
    humor_versions,
    labelled_rows,
    set_body,
    version,
)

API = "/api/v1"


def outcome(checks: dict[str, Any], code: str) -> dict[str, Any]:
    return next(o for o in checks["outcomes"] if o["code"] == code)


async def create(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets", json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


async def checks_of(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets/{set_id}/checks")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def bind(version_id: str, run_id: str) -> None:
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        row.bindings = [{"kind": "label_run", "id": run_id}]
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()


def built_from(child: str, parent: str) -> None:
    with sync_session_factory()() as db:
        db.add(VersionInput(version_id=child, position=0, kind="version", input_version_id=parent))
        db.commit()


def ood_rows() -> dict[str, list[dict[str, Any]]]:
    return {"test": labelled_rows(120, seed=3, tag="ood", pairs=True)}


# --- D-7 reads the lineage ----------------------------------------------------------------------


async def test_d7_refuses_a_labeler_bound_on_an_ancestor(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    run = make_run(versions["ood"], {})
    bind(versions["ood"], run.id)
    child = version(ood_rows(), dataset_name="humicroedit-repaired")
    built_from(child, versions["ood"])
    created = await create(client, set_body({**versions, "ood": child}))
    ood_role = next(r for r in created["roles"] if r["role"] == "ood_eval")

    checks = await checks_of(client, created["id"])
    d7 = outcome(checks, "D-7")

    assert d7["outcome"] == "refused", d7
    assert not checks["send_allowed"]
    (labeler,) = d7["details"]["labelers"]
    assert labeler["verdict"] == "none"
    assert labeler["roles"] == [ood_role["id"]]  # only the role whose lineage holds it
    assert ood_role["id"] not in d7["details"]["no_record_needed"]


async def test_d7_stays_green_with_no_labeler_in_the_lineage(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """The control: the same child and parent with nothing bound, so the walk is what decides."""
    versions = humor_versions()
    child = version(ood_rows(), dataset_name="humicroedit-repaired")
    built_from(child, versions["ood"])
    created = await create(client, set_body({**versions, "ood": child}))
    d7 = outcome(await checks_of(client, created["id"]), "D-7")
    assert d7["outcome"] == "green", d7
    assert d7["details"]["labelers"] == []


# --- a dataset's own role column ----------------------------------------------------------------


def versions_with_role(*, predicts_label: bool) -> dict[str, str]:
    def rows(n: int, seed: int, tag: str) -> list[dict[str, Any]]:
        out = labelled_rows(n, seed=seed, tag=tag)
        for i, row in enumerate(out):
            # the label alternates with i, so i // 2 says nothing about it
            first = row["label"] == out[1]["label"] if predicts_label else (i // 2) % 2 == 0
            row["role"] = "narrator" if first else "speaker"
        return out

    versions = humor_versions()
    versions["train"] = version(
        {"train": rows(200, 1, "tr"), "test": rows(60, 2, "te")},
        dataset_name="humor-with-role",
        held_out={"test": True},
    )
    return versions


def audit_columns(version_id: str) -> dict[str, dict[str, Any]]:
    with sync_session_factory()() as db:
        reports = db.query(VersionReport).filter(VersionReport.kind == "shortcut_audit").all()
    (report,) = [
        r
        for r in reports
        if len(r.inputs) > 1 and all(i["version_id"] == version_id for i in r.inputs)
    ]
    return {c["column"]: c for c in report.result["columns"]}


async def test_a_dataset_role_column_does_not_break_the_audit_and_both_are_audited(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = versions_with_role(predicts_label=False)
    created = await create(client, set_body(versions))
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "green", d3

    columns = audit_columns(versions["train"])
    assert columns["role"]["kind"] == "metadata"  # the dataset's own, a shortcut like any other
    assert columns["audit_role"]["kind"] == "derived"  # which role the row came from


async def test_a_dataset_role_column_that_predicts_the_label_refuses(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """The real column keeps its values: renaming it away would make this shortcut invisible."""
    versions = versions_with_role(predicts_label=True)
    created = await create(client, set_body(versions))
    d3 = outcome(await checks_of(client, created["id"]), "D-3")
    assert d3["outcome"] == "refused", d3
    assert "'role'" in d3["reason"], d3


def test_the_load_keeps_both_columns_apart(clean_db: None, data_dir: Path) -> None:
    versions = versions_with_role(predicts_label=False)
    with sync_session_factory()() as db:
        row = db.get(Version, versions["train"])
        assert row is not None
        path = next(s["path"] for s in row.splits if s["name"] == "train")
    full = str(data_dir / path)
    own = set(pq.read_table(full, columns=["role"]).column("role").to_pylist())
    source = InputFiles(
        ReportInput(versions["train"], "train", "the-train-role"), [("train", full)]
    )

    table = load_table([source], ["role"], cross_role=True)

    assert set(table.column("role").to_pylist()) == own == {"narrator", "speaker"}
    assert set(table.column("audit_role").to_pylist()) == {"the-train-role"}


def test_a_stored_report_keeps_the_name_role_when_nothing_clashes() -> None:
    assert cross_role_column(["text", "label"]) == "role"
    assert cross_role_column(["text", "role"]) == "audit_role"
    assert cross_role_column(["role", "audit_role"]) == "audit_audit_role"


# --- numeric work is off the greenlet ------------------------------------------------------------


def where() -> tuple[bool, bool]:
    """(on a root greenlet, in a worker thread)."""
    return getcurrent().parent is None, threading.current_thread() is not threading.main_thread()


def test_off_greenlet_hops_only_from_a_greenlet() -> None:
    assert off_greenlet(where) == (True, False)  # nothing to leave: called in place

    seen: list[Any] = []
    greenlet(lambda: seen.append(off_greenlet(where))).switch()
    assert seen == [(True, True)]
    assert off_greenlet(lambda a, *, b: a + b, 1, b=2) == 3


def test_off_greenlet_raises_what_the_call_raises() -> None:
    def boom() -> None:
        raise ValueError("from the thread")

    failures: list[BaseException] = []

    def run() -> None:
        try:
            off_greenlet(boom)
        except ValueError as exc:
            failures.append(exc)

    greenlet(run).switch()
    assert [str(f) for f in failures] == ["from the thread"]


async def test_run_in_own_session_commits_on_success_and_not_on_failure(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """Asserted with a write of its own: the set services commit for themselves and would hide it."""
    insert = text(
        "INSERT INTO dw_app_settings (key, value, is_sensitive, category) "
        "VALUES (:key, 'x', false, 'test')"
    )

    def stored(key: str) -> bool:
        with sync_session_factory()() as db:
            found = db.execute(
                text("SELECT 1 FROM dw_app_settings WHERE key = :key"), {"key": key}
            ).first()
            return found is not None

    def writes(session: Any) -> str:
        session.execute(insert, {"key": "kept"})
        return "answer"

    def fails(session: Any) -> None:
        session.execute(insert, {"key": "lost"})
        raise ValueError("after the write")

    assert await run_in_own_session(writes) == "answer"
    assert stored("kept")
    with pytest.raises(ValueError, match="after the write"):
        await run_in_own_session(fails)
    assert not stored("lost")


async def test_a_length_profile_is_measured_off_the_greenlet_on_a_read(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached profile is measured on the first reader, and that can be a GET on ``run_sync``."""
    versions = humor_versions()
    calls: list[tuple[bool, bool]] = []
    real = length.profile

    def spy(lengths: Any) -> Any:
        calls.append(where())
        return real(lengths)

    monkeypatch.setattr(length, "profile", spy)

    def read(session: Any) -> int:
        row = session.get(Version, versions["ood"])
        return int(set_service.profile_for(session, row, "test", "text").n)

    async with async_session_factory()() as db:
        assert await db.run_sync(read) == 120
        assert await db.run_sync(read) == 120  # stored, so measured once
    assert calls == [(True, True)]


async def test_the_check_route_runs_off_the_greenlet(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await create(client, set_body(humor_versions()))
    calls: list[tuple[str, tuple[bool, bool]]] = []
    real = set_service.run_checks

    def spy(session: Any, set_id: str) -> dict[str, Any]:
        calls.append((set_id, where()))
        return real(session, set_id)

    monkeypatch.setattr(set_service, "run_checks", spy)
    checks = await checks_of(client, created["id"])
    assert calls == [(created["id"], (True, True))]
    assert {o["code"] for o in checks["outcomes"]} >= {"D-1", "D-7"}


async def test_create_and_change_run_off_the_greenlet_and_commit(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[bool, bool]]] = []
    create_set, update_set = set_service.create_set, set_service.update_set

    def spy_create(session: Any, data: dict[str, Any], who: Any, **options: Any) -> Any:
        calls.append(("create", where()))
        return create_set(session, data, who, **options)

    def spy_update(session: Any, set_id: str, data: dict[str, Any], **options: Any) -> Any:
        calls.append(("update", where()))
        return update_set(session, set_id, data, **options)

    monkeypatch.setattr(set_service, "create_set", spy_create)
    monkeypatch.setattr(set_service, "update_set", spy_update)

    created = await create(client, set_body(humor_versions()))
    changed = await client.patch(
        f"{API}/detector-sets/{created['id']}", json={"description": "changed"}
    )
    assert changed.status_code == 200, changed.text
    assert calls == [("create", (True, True)), ("update", (True, True))]

    read = await client.get(f"{API}/detector-sets/{created['id']}")  # another session sees both
    assert read.status_code == 200 and read.json()["description"] == "changed"


async def test_a_refused_create_writes_nothing(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    body = set_body(humor_versions())
    assert (await client.post(f"{API}/detector-sets", json=body)).status_code == 201
    second = await client.post(f"{API}/detector-sets", json=body)  # the name is taken
    assert second.status_code == 409, second.text
    listed = await client.get(f"{API}/detector-sets")
    assert listed.json()["total"] == 1


async def test_both_send_routes_run_off_the_greenlet(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sent:
        id = "dsn_x"
        set_id = "dts_x"
        job_id = "job_x"
        state = "pending"

    calls: list[tuple[str, tuple[bool, bool], Any]] = []

    def spy_start(session: Any, set_id: str, **kwargs: Any) -> Sent:
        calls.append(("start", where(), set_id))
        return Sent()

    def spy_group(session: Any, set_ids: list[str], **kwargs: Any) -> tuple[str, list[Sent]]:
        calls.append(("group", where(), list(set_ids)))
        return "grp_x", [Sent()]

    monkeypatch.setattr(send_service, "start", spy_start)
    monkeypatch.setattr(send_service, "start_group", spy_group)

    one = await client.post(f"{API}/detector-sets/dts_x/send", json={"visibility": "private"})
    assert one.status_code == 202, one.text
    many = await client.post(
        f"{API}/detector-sets/send", json={"set_ids": ["dts_x"], "visibility": "private"}
    )
    assert many.status_code == 202, many.text
    assert calls == [("start", (True, True), "dts_x"), ("group", (True, True), ["dts_x"])]


async def test_an_inline_report_computes_off_the_greenlet_under_run_sync(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The funnel, for a caller on ``run_sync`` no route change reaches. Computed once, then read."""
    versions = humor_versions()
    inputs = [(versions["train"], "train", "a"), (versions["ood"], "test", "b")]
    calls: list[tuple[list[str], tuple[bool, bool]]] = []
    real = leakage_service.compute_leakage

    def spy(session: Any, report_inputs: Any, params: Any, seed: int) -> Any:
        calls.append(([str(i.role) for i in report_inputs], where()))
        return real(session, report_inputs, params, seed)

    monkeypatch.setattr(leakage_service, "compute_leakage", spy)

    async with async_session_factory()() as db:
        for _ in range(2):
            await db.run_sync(lambda s: curation_seam.check_leakage(inputs, session=s))
    assert calls == [(["a", "b"], (True, True))]
