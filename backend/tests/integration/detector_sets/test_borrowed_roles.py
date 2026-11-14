"""ENH-006 G21: a training-only set borrows a sibling's evaluation, OOD and calibration roles.

The gap: D-1 refused a set without its own in-distribution, out-of-distribution and calibration
roles, so D125's arm B (version 48's training rows, evaluated on version 47's views) re-registered
version 47's five evaluation views in miWorkbench under ``-b`` names. Operator decision 2026-11-08:
allow a training-only set that borrows a sibling set's evaluation, out-of-distribution and
calibration roles; the send reuses the views already in miWorkbench; R-03.49 is amended.

Driven end to end: the sibling is sent through the real send worker against the fake miWorkbench,
then the borrowing set is checked and sent, and miWorkbench's calls are counted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from src.core.database import sync_session_factory
from src.models.detector_set import DetectorSet
from src.services.detector_sets import plan, results_service, send_service
from tests.support.detector_fixtures import MAPPING, labelled_rows, version
from tests.support.mcp_live import call, production_mcp
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, ready_set, repos_for, start_send

KEY = "fixture-alpha"
MEANING = "Most readers would find the text funny"
S = f"{API}/detector-sets"


def train_only(name: str, train_version: str, sibling: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "positive_meaning": MEANING,
        "behaviour_key": KEY,
        "borrows_from_set_id": sibling,
        "roles": [
            {
                "role": "train",
                "version_id": train_version,
                "split": "train",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
            }
        ],
    }
    body.update(over)
    return body


async def sent_sibling(client: httpx.AsyncClient, sender: SendDriver) -> tuple[str, str]:
    """Arm A: a complete set, sent; returns (set id, send id)."""
    set_id, _versions, _repos = await ready_set(client, sender, {"behaviour_key": KEY})
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    return set_id, started["send_id"]


def d(result: dict[str, Any], code: str) -> dict[str, Any]:
    found: dict[str, Any] = next(o for o in result["outcomes"] if o["code"] == code)
    return found


async def checks_of(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    response = await client.post(f"{S}/{set_id}/checks")
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


async def test_a_training_only_arm_reuses_the_siblings_views(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sibling, sibling_send = await sent_sibling(client, sender)
    views_a = {
        s.unit_key: s.probe_dataset_id
        for (kind, _), s in sender.steps(sibling_send).items()
        if kind == "register"
    }
    assert len(views_a) == 4
    registered_before = sender.mistudio.count("POST", "/api/v1/probe-monitors/datasets")
    downloads_before = sender.mistudio.count("POST", "/api/v1/datasets/download")

    train_b = version({"train": labelled_rows(200, seed=21, tag="armb")}, dataset_name="arm-b")
    created = await client.post(S, json=train_only("arm-b", train_b, sibling))
    assert created.status_code == 201, created.text
    arm_b = created.json()
    assert arm_b["borrows_from_set_id"] == sibling
    assert [r["role"] for r in arm_b["roles"]] == ["train"]
    assert sorted(r["role"] for r in arm_b["borrowed_roles"]) == [
        "calibration_negatives",
        "id_test",
        "ood_eval",
    ]
    # The borrowed roles keep the names the sibling registered them under.
    assert all(r["view_name"].startswith("humor-set - ") for r in arm_b["borrowed_roles"])

    result = await checks_of(client, arm_b["id"])
    d1 = d(result, "D-1")
    assert d1["outcome"] == "green", d1
    assert d1["details"]["sibling_send_id"] == sibling_send
    borrowed_ids = {r["id"] for r in arm_b["borrowed_roles"]}
    assert d1["details"]["borrowed_views"] == {
        k: v for k, v in views_a.items() if k in borrowed_ids
    }
    assert "R-03.49 (as amended" in d1["source"]
    assert result["send_allowed"] is True, result["first_refusal"]

    # The send: B's training version only is published, downloaded and registered.
    first = await client.post(f"{S}/{arm_b['id']}/send", json={"namespace": NAMESPACE})
    assert first.status_code == 409 and first.json()["error"]["code"] == "builds_pending"
    sender.publisher.run_publish_tasks()
    repo_b = repos_for({"train": train_b})[train_b]
    sender.mistudio.values[(repo_b, "train")] = {"humorous": 100, "not_humorous": 100}
    started = await start_send(client, arm_b["id"])
    assert sender.run(started["job_id"])["status"] == "completed"
    regs = sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")[registered_before:]
    assert [(c.body["role"], c.body["name"]) for c in regs] == [("train", "arm-b - training rows")]
    downloads = sender.mistudio.calls("POST", "/api/v1/datasets/download")[downloads_before:]
    assert [(c.body["repo_id"], c.body["split"]) for c in downloads] == [(repo_b, "train")]

    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    by_role = {r["role"]: r["id"] for r in arm_b["borrowed_roles"]}
    run = got["run_request"]
    own_train = next(
        s.probe_dataset_id
        for (kind, _), s in sender.steps(started["send_id"]).items()
        if kind == "register"
    )
    assert run == {
        "train_dataset_id": own_train,
        "eval_dataset_ids": [views_a[by_role["id_test"]], views_a[by_role["ood_eval"]]],
        "calibration_dataset_id": views_a[by_role["calibration_negatives"]],
    }
    assert {v["from_send_id"] for v in got["borrowed_views"]} == {sibling_send}

    # Results read the borrowed views as this set's roles, joined on the sibling's downloads.
    with sync_session_factory()() as db:
        send = send_service.get_send(db, started["send_id"])
        view = results_service.send_view(db, send)
    for role, view_id in ((r, views_a[i]) for r, i in by_role.items()):
        assert view.role_by_view[view_id]["role"] == role
        assert view.role_by_view[view_id]["send_id"] == sibling_send
    assert view.train_view == own_train


async def test_borrowed_roles_are_checked_against_the_training_rows(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """D-4/D-11 prove B's training rows against the BORROWED views: a borrowing set that trains
    on the sibling's held-out rows is refused."""
    sibling, _ = await sent_sibling(client, sender)
    sibling_out = (await client.get(f"{S}/{sibling}")).json()
    test_role = next(r for r in sibling_out["roles"] if r["role"] == "id_test")
    leaky = train_only(
        "arm-leaky",
        test_role["version_id"],
        sibling,
        roles=[
            {
                "role": "train",
                "version_id": test_role["version_id"],
                "split": "test",
                "input_column": "text",
                "label_column": "label",
                "label_mapping": MAPPING,
            }
        ],
    )
    created = await client.post(S, json=leaky)
    assert created.status_code == 201, created.text
    result = await checks_of(client, created.json()["id"])
    assert result["send_allowed"] is False
    assert d(result, "D-4")["outcome"] == "refused", d(result, "D-4")


async def test_d1_refuses_a_sibling_that_cannot_lend_with_each_reason(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    train_b = version({"train": labelled_rows(200, seed=22, tag="armc")}, dataset_name="arm-c")
    # Not yet sent: miWorkbench holds none of the sibling's views.
    unsent, _versions, _repos = await ready_set(client, sender, {"behaviour_key": KEY})
    early = (await client.post(S, json=train_only("arm-early", train_b, unsent))).json()
    d1 = d(await checks_of(client, early["id"]), "D-1")
    assert d1["outcome"] == "refused"
    assert any("has no completed send" in p for p in d1["details"]["problems"]), d1

    sent = await start_send(client, unsent)
    assert sender.run(sent["job_id"])["status"] == "completed"
    assert d(await checks_of(client, early["id"]), "D-1")["outcome"] == "green"

    cases = {
        "arm-other-key": ({"behaviour_key": "fixture-beta"}, "the behaviours differ"),
        "arm-other-meaning": ({"positive_meaning": "something else"}, "positive meanings differ"),
        "arm-other-definition": (
            {
                "behaviour_definition": {
                    "key": KEY,
                    "definition_version": 7,
                    "positive_meaning": MEANING,
                    "verified": False,
                    "source": "supplied",
                }
            },
            "the definitions differ",
        ),
    }
    for name, (over, words) in cases.items():
        made = await client.post(S, json=train_only(name, train_b, unsent, **over))
        assert made.status_code == 201, made.text
        d1 = d(await checks_of(client, made.json()["id"]), "D-1")
        assert d1["outcome"] == "refused", (name, d1)
        assert any(words in p for p in d1["details"]["problems"]), (name, d1)

    # A borrowing set holds only its training role.
    sibling_out = (await client.get(f"{S}/{unsent}")).json()
    own_test = next(r for r in sibling_out["roles"] if r["role"] == "id_test")
    keys = ("role", "version_id", "split", "input_column", "label_column", "label_mapping")
    extra = train_only("arm-extra", train_b, unsent)
    extra["roles"].append({k: own_test[k] for k in keys})
    made = await client.post(S, json=extra)
    d1 = d(await checks_of(client, made.json()["id"]), "D-1")
    assert any("holds only its training role" in p for p in d1["details"]["problems"]), d1

    # No chains: borrowing from a set that itself borrows.
    chained = await client.post(S, json=train_only("arm-chain", train_b, early["id"]))
    d1 = d(await checks_of(client, chained.json()["id"]), "D-1")
    assert any("borrows its own evaluation roles" in p for p in d1["details"]["problems"]), d1

    # The sibling's roles replaced after its send: their views are not registered as they are now.
    roles_now = [
        {
            k: r[k]
            for k in (*keys, "pair_column", "negatives_basis")
            if r.get(k) is not None or k in keys
        }
        for r in sibling_out["roles"]
    ]
    changed = await client.patch(f"{S}/{unsent}", json={"roles": roles_now})
    assert changed.status_code == 200, changed.text
    d1 = d(await checks_of(client, early["id"]), "D-1")
    assert any("did not register" in p for p in d1["details"]["problems"]), d1


async def test_a_set_cannot_borrow_from_itself_or_an_unknown_set(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    train_b = version({"train": labelled_rows(40, seed=23, tag="armd")}, dataset_name="arm-d")
    unknown = await client.post(S, json=train_only("arm-unknown", train_b, "dts_missing"))
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "set_not_found"
    made = await client.post(
        S, json=train_only("arm-self", train_b, "dts_missing", borrows_from_set_id=None)
    )
    assert made.status_code == 201, made.text
    me = made.json()["id"]
    refused = await client.patch(f"{S}/{me}", json={"borrows_from_set_id": me})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "borrow_invalid"


async def test_the_mcp_tools_carry_the_sibling(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: Any,
) -> None:
    sibling, _ = await sent_sibling(client, sender)
    train_b = version({"train": labelled_rows(200, seed=24, tag="arme")}, dataset_name="arm-e")
    body = train_only("arm-mcp", train_b, sibling)
    mcp, mcp_client = production_mcp(monkeypatch)
    try:
        made = await call(mcp, "dataworks_create_detector_set", body)
        assert made["borrows_from_set_id"] == sibling
        cleared = await call(
            mcp, "dataworks_update_detector_set", {"set_id": made["id"], "borrows_from_set_id": ""}
        )
        assert cleared["borrows_from_set_id"] is None and cleared["borrowed_roles"] == []
    finally:
        await mcp_client.close()


def test_the_approval_digest_binds_the_borrowed_views() -> None:
    base = {"publish_units": [], "roles": [], "mistudio_base_url": "http://m"}
    plain = plan.approval_digest(base, set_id="dts_1", snapshot_sha256="s")
    one = {**base, "borrowed_views": [{"role_id": "r1", "probe_dataset_id": "v1"}]}
    other = {**base, "borrowed_views": [{"role_id": "r1", "probe_dataset_id": "v2"}]}
    first = plan.approval_digest(one, set_id="dts_1", snapshot_sha256="s")
    assert first != plan.approval_digest(other, set_id="dts_1", snapshot_sha256="s")
    assert first != plain
    assert plain == plan.approval_digest(
        {**base, "borrowed_views": []}, set_id="dts_1", snapshot_sha256="s"
    ), "a send that borrows nothing keeps its pre-G21 digest"


async def test_only_a_completed_sibling_send_lends_its_views(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """A sibling send that failed part-way registered some views; they are not lent, and the
    send itself refuses (the guard behind D-1) rather than send without them."""
    sibling, versions, repos = await ready_set(client, sender, {"behaviour_key": KEY})
    sender.mistudio.wrong_counts[(repos[versions["ood"]], "test")] = {
        "positive": 1,
        "negative": 1,
        "excluded": 0,
    }
    failed = await start_send(client, sibling)
    assert sender.run(failed["job_id"])["status"] == "failed"
    registered = [
        s for (kind, _), s in sender.steps(failed["send_id"]).items() if kind == "register"
    ]
    assert any(s.probe_dataset_id for s in registered), "the fixture must register some views"
    train_b = version({"train": labelled_rows(200, seed=25, tag="armf")}, dataset_name="arm-f")
    made = (await client.post(S, json=train_only("arm-failed", train_b, sibling))).json()
    d1 = d(await checks_of(client, made["id"]), "D-1")
    assert any("has no completed send" in p for p in d1["details"]["problems"]), d1
    from src.services.detector_sets.errors import DetectorSetError

    with sync_session_factory()() as db:
        row = db.get(DetectorSet, made["id"])
        assert row is not None
        try:
            send_service.borrowed_views(db, row)
        except DetectorSetError as exc:
            assert exc.code == "send_refused"
            assert sorted(exc.details["roles_without_views"]) == sorted(
                r["id"] for r in made["borrowed_roles"]
            )
        else:
            raise AssertionError("a sibling with no completed send lent its views")


async def test_paired_scores_join_on_the_siblings_send(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: Any,
) -> None:
    """A borrowed view's rows were downloaded by the SIBLING's send: the per-row join reads that
    send, once, for that view."""
    from src.services.detector_sets import mistudio_scores

    sibling, sibling_send = await sent_sibling(client, sender)
    train_b = version({"train": labelled_rows(200, seed=26, tag="armg")}, dataset_name="arm-g")
    made = (await client.post(S, json=train_only("arm-paired", train_b, sibling))).json()
    sender.publisher.run_publish_tasks()
    await client.post(f"{S}/{made['id']}/send", json={"namespace": NAMESPACE})
    sender.publisher.run_publish_tasks()
    repo_b = repos_for({"train": train_b})[train_b]
    sender.mistudio.values[(repo_b, "train")] = {"humorous": 100, "not_humorous": 100}
    started = await start_send(client, made["id"])
    assert sender.run(started["job_id"])["status"] == "completed"
    ood = next(v for v in send_service.borrowed_of(sender.send(started["send_id"]))
               if v["role"] == "ood_eval")  # fmt: skip
    joined: list[tuple[str, str]] = []

    def spy(session: Any, send_id: str, view_id: str, role: Any, served: Any) -> Any:
        joined.append((send_id, view_id))
        return mistudio_scores.Refusal("spied")

    monkeypatch.setattr(mistudio_scores, "join", spy)
    collected = results_service.Collected(per_row_scores=True)
    collected.scores[("p1", "e1")] = {"dataset_id": ood["probe_dataset_id"]}
    with sync_session_factory()() as db:
        view = results_service.send_view(db, send_service.get_send(db, started["send_id"]))
        found, reason = results_service._mistudio_source(  # noqa: SLF001
            db,
            {"probe_id": "p1"},
            {"probe_dataset_id": ood["probe_dataset_id"], "evaluation_id": "e1"},
            view,
            collected,
        )
    assert found is None and reason == "spied"
    assert joined == [(sibling_send, ood["probe_dataset_id"])]
