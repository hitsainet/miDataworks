"""A sent view is ready to break down (ENH-006 D116 G11).

Views from a send arrived with ``group_columns: null`` though the manifest lists ``breakdown_columns``, so
the first probe could not be read by ``kind``, ``framing``, ``form`` or ``genre`` until each view was
updated by hand. The REAL send worker and 008's REAL publish against the fake miWorkbench, which enforces the
columns as miWorkbench's ``services/view_columns`` does. Expected columns are read from the manifest the
publish really wrote; distinct counts from the fixture's rows, not through the code under test.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.models.publish import Publish
from src.models.version import Version
from src.services.detector_sets import capabilities, group_columns, plan
from tests.support.detector_fixtures import (
    ROLES,
    headline_negatives,
    labelled_rows,
    set_body,
    version,
)
from tests.support.publish_fixtures import store_token
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, repos_for, start_send

KINDS = ("pos", "masked", "other_number")
FRAMINGS = ("named_person", "discussed")
BEHAVIOUR_ROLES = {
    **ROLES,
    "kind": "metadata",
    "framing": "metadata",
    "form": "metadata",
    "genre": "metadata",
    "carrier_id": "metadata",
    "planned_batch": "metadata",
    "unlisted_note": "metadata",
}
REGISTER = "/api/v1/probe-monitors/datasets"


def carriers(rows: list[dict[str, Any]], tag: str) -> list[dict[str, Any]]:
    return [
        {
            **row,
            # by pairs of rows: labelled_rows alternates its label, and these must not predict it
            "kind": KINDS[(i // 2) % 3],
            "framing": FRAMINGS[(i // 2) % 2],
            "form": None if (i // 2) % 5 == 0 else "digits",
            "genre": f"genre-{(i // 2) % 4}",
            "carrier_id": f"{tag}-carrier-{i}",  # every row its own: a row id, not a group
            "planned_batch": f"batch-{(i // 2) % 90}",
            "unlisted_note": "not in the manifest's listing",
        }
        for i, row in enumerate(rows)
    ]


def _behaviour_version(splits: dict[str, list[dict[str, Any]]], name: str, **kw: Any) -> str:
    made = version(splits, dataset_name=name, roles=BEHAVIOUR_ROLES, **kw)
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.execute(
            text("UPDATE dw_versions SET behaviour_configuration = '{\"a\": 1}' WHERE id = :v"),
            {"v": made},
        )
        conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
    return made


def versions() -> dict[str, str]:
    train = _behaviour_version(
        {
            "train": carriers(labelled_rows(200, seed=1, tag="tr"), "tr"),
            "test": carriers(labelled_rows(60, seed=2, tag="te"), "te"),
        },
        "pilot-main",
        held_out={"test": True},
    )
    ood = _behaviour_version(
        {"test": carriers(labelled_rows(120, seed=3, tag="ood", pairs=True), "ood")}, "pilot-ood"
    )
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="plain-negatives")
    return {"train": train, "ood": ood, "cal": cal}


def _distinct_of(version_id: str, split: str) -> dict[str, int]:
    """What a downloaded split holds, read here without the code under test."""
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        path = next(s["path"] for s in row.splits if s["name"] == split)
    from src.core.storage import resolve_under_data_dir

    table = pq.read_table(resolve_under_data_dir(path)).to_pydict()
    return {column: len(set(values)) for column, values in table.items()}


async def _ready(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, dict[str, str], Any]:
    await store_token(client)
    made = versions()
    created = await client.post(f"{API}/detector-sets", json=set_body(made))
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]
    first = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE})
    assert first.status_code == 409, first.text
    assert first.json()["error"]["code"] == "builds_pending", first.json()
    sender.publisher.run_publish_tasks()
    repos = repos_for(made)
    sender.seed_values(repos, made)
    fake = sender.mistudio
    for key, split in (("train", "train"), ("train", "test"), ("ood", "test"), ("cal", "train")):
        fake.split_distinct[(repos[made[key]], split)] = _distinct_of(made[key], split)
    return set_id, made, repos


def _manifest_listing(send_id: str, version_id: str) -> list[str]:
    with sync_session_factory()() as db:
        publish = (
            db.query(Publish)
            .filter(Publish.send_id == send_id, Publish.version_id == version_id)
            .one()
        )
        extensions = (publish.published_manifest or {}).get("extensions") or {}
        return list(extensions.get("breakdown_columns") or [])


def _by_split(regs: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(c.body["role"], c.body["split"]): c.body for c in regs}


async def test_every_behaviour_role_declares_the_manifests_columns_once(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_group_columns = True
    set_id, made, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error

    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4, "one registration per role"
    assert sender.mistudio.count("PATCH", REGISTER) == 0, "nothing to update by hand afterwards"
    bodies = _by_split(regs)
    listing = _manifest_listing(started["send_id"], made["train"])
    assert {"kind", "framing", "form", "genre", "carrier_id", "planned_batch"} <= set(listing)
    assert (
        "unlisted_note" not in listing
    ), "the fixture's control: a column the manifest does not list"
    train_columns = bodies[("train", "train")]["group_columns"]
    assert train_columns[:4] == ["genre", "form", "framing", "kind"], "the plan's columns first"
    # labelled_rows gives each unpaired row its own pair_id: a row id, as carrier_id is
    assert set(train_columns) == set(listing) - {"carrier_id", "pair_id"}
    paired = next(c.body for c in regs if c.body.get("distribution") == "out_of_distribution")
    assert "pair_id" in paired["group_columns"], "two rows to a pair there: that one groups"
    for body in regs:
        columns = body.body.get("group_columns")
        if body.body["role"] == "calibration":
            assert columns is None, "a version that is not a behaviour build lists nothing"
            continue
        assert "carrier_id" not in columns
        assert "unlisted_note" not in columns
        assert {"kind", "framing", "form", "genre"} <= set(columns)
    # miWorkbench's view holds them, so the first breakdown works with nothing done by hand
    train_view = next(v for v in sender.mistudio.views.values() if v["role"] == "train")
    assert train_view["group_columns"] == train_columns


async def test_the_send_says_what_each_view_left_out_and_the_approval_binds_the_columns(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_group_columns = True
    set_id, made, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    train_id = next(r["id"] for r in got["snapshot"]["roles"] if r["role"] == "train")
    report = got["group_columns"][train_id]
    assert "kind" in report["declared"]
    own_value = "every row holds its own value, so it groups nothing"
    assert report["left_out"] == {"carrier_id": own_value, "pair_id": own_value}
    send = sender.send(started["send_id"])
    template = next(r for r in send.plan["roles"] if r["role_id"] == train_id)["body"]
    assert template["group_columns"] == report["declared"]
    bare = {k: v for k, v in template.items() if k != "group_columns"}
    other = {**send.plan, "roles": [{**send.plan["roles"][0], "body": bare}]}
    same = {**send.plan, "roles": [{**send.plan["roles"][0], "body": template}]}
    assert plan.approval_digest(other, set_id=set_id, snapshot_sha256="a") != plan.approval_digest(
        same, set_id=set_id, snapshot_sha256="a"
    )


async def test_a_column_over_miWorkbenchs_limit_is_left_out_not_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """90 distinct values on train's 200 rows. With the limit at 80 on both sides, declaring it would be
    refused ``too_many_values`` and fail the whole role. The rule reads each role's own split: 30 values
    on the 60 rows of the in-distribution test are under it, so that view declares the column."""
    monkeypatch.setattr(group_columns, "GROUP_COLUMN_MAX_VALUES", 80)
    sender.mistudio.serves_group_columns = True
    sender.mistudio.group_column_max_values = 80
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error
    bodies = _by_split(sender.mistudio.calls("POST", REGISTER))
    assert "planned_batch" not in bodies[("train", "train")]["group_columns"]
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    train_id = next(r["id"] for r in got["snapshot"]["roles"] if r["role"] == "train")
    assert (
        "over miWorkbench's limit of 80"
        in got["group_columns"][train_id]["left_out"]["planned_batch"]
    )
    test_body = next(
        c.body
        for c in sender.mistudio.calls("POST", REGISTER)
        if c.body["split"] == "test" and c.body.get("distribution") == "in_distribution"
    )
    assert "planned_batch" in test_body["group_columns"]


async def test_a_miWorkbench_that_does_not_serve_it_gets_no_key_and_the_step_says_why(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    assert sender.mistudio.serves_group_columns is False
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4 and all("group_columns" not in c.body for c in regs)
    notes = [
        s.behaviour
        for (kind, _), s in sender.steps(started["send_id"]).items()
        if kind == "register" and s.behaviour and "group_columns" in s.behaviour
    ]
    assert len(notes) == 3 and all("not served" in n["group_columns"] for n in notes)


async def test_a_reused_view_keeps_its_id_and_gains_only_what_it_lacks_additively(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """The operator's actual case: views already registered with none, some updated by hand. A re-send
    registers nothing new, so runs on those views stay readable, and declares the rest by the route
    that adds; a column declared by hand stays, because miWorkbench refuses a removal."""
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    fake = sender.mistudio
    before = dict(fake.views)
    assert fake.count("POST", REGISTER) == 4
    fake.serves_group_columns = True
    capabilities.clear_cache()
    train_id = next(k for k, v in fake.views.items() if v["role"] == "train")
    fake.views[train_id]["group_columns"] = ["by_hand", "kind"]
    repo_split = next(k for k in fake.split_distinct if k[1] == "train" and "main" in k[0])
    fake.split_distinct[repo_split]["by_hand"] = 3

    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    assert fake.count("POST", REGISTER) == 4, "not one view registered again"
    assert set(fake.views) == set(before)
    patches = fake.calls("PATCH", REGISTER)
    assert len(patches) == 3, "the three behaviour views; the plain one has none to add"
    train_patch = next(p for p in patches if train_id in p.path)
    sent = train_patch.body["group_columns"]
    assert sent[:2] == ["by_hand", "kind"], "what was declared comes first and stays"
    assert (
        sent.count("kind") == 1 and "carrier_id" not in sent and {"genre", "framing"} <= set(sent)
    )
    assert set(train_patch.body) == {"group_columns"}
    steps = sender.steps(again["send_id"])
    registers = [s for (kind, _), s in steps.items() if kind == "register"]
    assert {s.state for s in registers} == {"reused"}
    assert any("genre" in (s.behaviour or {}).get("group_columns_added", []) for s in registers)

    third = await start_send(client, set_id)
    assert sender.run(third["job_id"])["status"] == "completed"
    assert fake.count("PATCH", REGISTER) == 3, "nothing left to add: no further call"
    assert fake.count("POST", REGISTER) == 4


async def test_with_no_update_route_a_reused_view_stays_as_it_is_and_the_send_completes(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    fake = sender.mistudio
    fake.serves_group_columns, fake.serves_group_columns_route = True, False
    capabilities.clear_cache()
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    assert fake.count("PATCH", REGISTER) == 0 and fake.count("POST", REGISTER) == 4


async def test_a_view_with_little_room_gains_only_what_fits(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Hand-declared columns stay, and miWorkbench refuses ``over_cap`` past its limit."""
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    fake = sender.mistudio
    fake.serves_group_columns = True
    capabilities.clear_cache()
    train_id = next(k for k, v in fake.views.items() if v["role"] == "train")
    by_hand = [f"hand_{i}" for i in range(group_columns.VIEW_MAX_GROUP_COLUMNS - 1)]
    fake.views[train_id]["group_columns"] = by_hand
    repo_split = next(k for k in fake.split_distinct if k[1] == "train" and "main" in k[0])
    fake.split_distinct[repo_split].update(dict.fromkeys(by_hand, 2))
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    patch = next(p for p in fake.calls("PATCH", REGISTER) if train_id in p.path)
    assert patch.body["group_columns"] == [*by_hand, "genre"]


async def test_a_view_miWorkbench_no_longer_has_is_registered_afresh(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_group_columns = True
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    fake = sender.mistudio
    gone = next(k for k, v in fake.views.items() if v["role"] == "train")
    del fake.views[gone]
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    regs = fake.calls("POST", REGISTER)
    assert len(regs) == 5 and regs[-1].body["role"] == "train"
    assert "kind" in regs[-1].body["group_columns"]


async def test_a_refused_update_fails_the_role_by_name(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await _ready(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    fake = sender.mistudio
    fake.serves_group_columns = True
    capabilities.clear_cache()
    fake.group_column_max_values = 1  # lowered in miWorkbench since this plan was made
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "failed"
    error = sender.send(again["send_id"]).error
    assert error["code"] == "registration_refused"
    assert "too_many_values" in error["message"]


# --- which columns, on the file itself ----------------------------------------------------------


def _file(tmp: Path, columns: dict[str, list[Any]]) -> Path:
    from src.core.storage import data_dir

    path = data_dir() / "group-columns-test" / tmp.name / "s.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(columns), path)
    return path


def test_the_limit_is_exact_and_a_null_counts_as_a_value(data_dir: Path, tmp_path: Path) -> None:
    n = group_columns.GROUP_COLUMN_MAX_VALUES
    at = [f"v{i}" for i in range(n)] + ["v0"]
    over = [f"v{i}" for i in range(n + 1)]
    with_null = [f"v{i}" for i in range(n)] + [None]
    path = _file(tmp_path, {"sq_at": at, "sq_over": [*over[:-1], over[-1]], "sq_null": with_null})
    # sq_over and sq_null hold n+1 values on n+1 rows, so read them on one more repeated row too
    longer = _file(
        tmp_path / "longer",
        {"sq_over": [*over, "v0"], "sq_null": [*with_null, "v0"], "sq_at": [*at, "v1"]},
    )
    chosen = group_columns.choose(longer, ["sq_at", "sq_over", "sq_null"])
    assert chosen.columns == ("sq_at",)
    assert "over miWorkbench's limit" in chosen.left_out["sq_over"]
    assert "over miWorkbench's limit" in chosen.left_out["sq_null"]
    assert path.exists()


def test_every_row_its_own_value_is_left_out_even_under_the_limit(
    data_dir: Path, tmp_path: Path
) -> None:
    path = _file(
        tmp_path,
        {
            "carrier_id": ["a", "b", "c", "d"],
            "kind": ["x", "x", "y", "y"],
            "pair_id": ["p", "p", "q", "r"],
        },
    )
    chosen = group_columns.choose(path, ["carrier_id", "kind", "pair_id"])
    assert chosen.columns == ("kind", "pair_id")
    assert list(chosen.left_out) == ["carrier_id"]


def test_past_the_cap_the_plans_columns_stay_and_one_value_columns_go_first(
    data_dir: Path, tmp_path: Path
) -> None:
    extra = {f"sq_{i:02d}": ["a", "a", "b", "b"] for i in range(40)}
    path = _file(
        tmp_path,
        {
            **extra,
            "behaviour_key": ["k", "k", "k", "k"],
            "genre": ["g", "g", "h", "h"],
            "kind": ["x", "x", "y", "y"],
        },
    )
    chosen = group_columns.choose(path, [*extra, "behaviour_key", "genre", "kind"])
    assert len(chosen.columns) == group_columns.VIEW_MAX_GROUP_COLUMNS
    assert chosen.columns[:2] == ("genre", "kind")
    assert (
        "behaviour_key" not in chosen.columns
    ), "one value groups nothing, so it goes before a usable one"
    assert "past the 32 columns" in chosen.left_out["behaviour_key"]
    assert Counter(chosen.columns).most_common(1)[0][1] == 1


def test_a_listed_column_this_split_lacks_is_left_out(data_dir: Path, tmp_path: Path) -> None:
    path = _file(tmp_path, {"kind": ["x", "x", "y"]})
    chosen = group_columns.choose(path, ["kind", "framing"])
    assert chosen.columns == ("kind",)
    assert chosen.left_out == {"framing": "not in this split"}


def test_nothing_listed_declares_nothing(data_dir: Path, tmp_path: Path) -> None:
    path = _file(tmp_path, {"kind": ["x", "x", "y"]})
    assert group_columns.choose(path, []) == group_columns.Chosen()


def test_a_reused_publish_is_read_from_its_stored_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The names are the manifest's, never a list here: a reused publish's listing may be older."""
    seen: list[str] = []

    def stored(session: Any, publish_id: str) -> dict[str, Any]:
        seen.append(publish_id)
        return {"extensions": {"breakdown_columns": ["genre", "only_in_that_manifest"]}}

    monkeypatch.setattr(group_columns.publish_service, "published_manifest_for", stored)
    assert group_columns.listed_columns(None, None, None, "pub_1") == [  # type: ignore[arg-type]
        "genre",
        "only_in_that_manifest",
    ]
    assert seen == ["pub_1"]


@pytest.mark.parametrize(
    ("properties", "patch", "columns", "route"),
    [
        (["group_columns"], True, True, True),
        (["group_columns"], False, True, False),
        ([], True, False, True),
    ],
)
def test_the_capabilities_read_the_served_schema(
    properties: list[str], patch: bool, columns: bool, route: bool
) -> None:
    document: dict[str, Any] = {
        "components": {
            "schemas": {"ProbeDatasetCreate": {"properties": {p: {} for p in properties}}}
        },
        "paths": (
            {"/api/v1/probe-monitors/datasets/{dataset_id}/columns": {"patch": {}}}
            if patch
            else {"/api/v1/probe-monitors/datasets/{dataset_id}/rows": {"get": {}}}
        ),
    }
    caps = capabilities.from_openapi("http://m", document)
    assert (caps.view_group_columns, caps.view_columns_route) == (columns, route)
