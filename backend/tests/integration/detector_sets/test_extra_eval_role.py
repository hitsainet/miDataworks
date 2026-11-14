"""An extra evaluation view on a set (ENH-006 D116 G7; FTASKS 20.16).

A set takes exactly one in-distribution role, and D-4 refuses rows shared across roles, so a human-graded
test set had to be published and registered outside its set. ``extra_eval`` (zero or more) goes to
miWorkbench as ``eval`` with ``in_distribution``, is exempt from D-1's counts, and D-4 refuses it only against
the training role. Through the live routes, the real checks and the REAL send worker against the fake
miWorkbench. The view's label is ``human_label``, not the set's ``label``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from src.core.database import sync_session_factory
from src.models.detector_set import DetectorSetRole
from src.services.detector_sets import checks as checks_module
from src.services.detector_sets.role_mapping import MISTUDIO_ROLE, leakage_counts
from tests.support.detector_fixtures import ROLES, humor_versions, labelled_rows, set_body, version
from tests.support.send_fixtures import (
    API,
    NAMESPACE,
    SendDriver,
    ready_set,
    repos_for,
    start_send,
)

HUMAN = {"yes": "positive", "no": "negative", "excluded": "excluded"}


def human_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows graded by a person: ``human_label`` disagrees with the set's own ``label`` on purpose."""
    out = []
    for i, row in enumerate(rows):
        answer = "excluded" if i % 7 == 0 else ("yes" if i % 2 else "no")
        kept = {k: v for k, v in row.items() if k != "pair_id"}
        out.append({**kept, "human_label": answer})
    return out


def view(rows: list[dict[str, Any]], name: str) -> str:
    """A version holding a human-graded split, ``test``."""
    return version(
        {"test": human_rows(rows)}, dataset_name=name, roles={**ROLES, "human_label": "metadata"}
    )


def extra_role(version_id: str, split: str = "test", **over: Any) -> dict[str, Any]:
    return {
        "role": "extra_eval",
        "version_id": version_id,
        "split": split,
        "input_column": "text",
        "label_column": "human_label",
        "label_mapping": HUMAN,
        **over,
    }


def unshared_extra(tag: str = "human", seed: int = 40) -> str:
    return view(labelled_rows(60, seed=seed, tag=tag), f"{tag}-eval")


def outcome(body: dict[str, Any], code: str) -> dict[str, Any]:
    return next(o for o in body["outcomes"] if o["code"] == code)


async def _create(client: httpx.AsyncClient, body: dict[str, Any]) -> str:
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def _check(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    got = await client.post(f"{API}/detector-sets/{set_id}/checks", params={"detail": "full"})
    assert got.status_code == 200, got.text
    answer: dict[str, Any] = got.json()
    return answer


async def test_zero_one_or_many_and_d1_does_not_count_them(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    bare = set_body(versions, name="no-extra")
    assert outcome(await _check(client, await _create(client, bare)), "D-1")["outcome"] == "green"
    many = set_body(versions, name="two-extra")
    many["roles"] += [extra_role(unshared_extra("one", 41)), extra_role(unshared_extra("two", 42))]
    set_id = await _create(client, many)
    body = await _check(client, set_id)
    assert outcome(body, "D-1")["outcome"] == "green"
    shown = (await client.get(f"{API}/detector-sets/{set_id}")).json()
    assert shown["role_counts"] == {
        "train": 1,
        "id_test": 1,
        "ood_eval": 1,
        "calibration_negatives": 1,
        "extra_eval": 2,
    }
    names = [r["view_name"] for r in shown["roles"] if r["role"] == "extra_eval"]
    assert len(set(names)) == 2, names
    assert all("extra evaluation" in n for n in names)


async def test_it_stands_in_for_no_required_role(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    body = set_body(versions, name="no-test")
    body["roles"] = [r for r in body["roles"] if r["role"] != "id_test"]
    body["roles"].append(extra_role(unshared_extra()))
    set_id = await _create(client, body)
    d1 = outcome(await _check(client, set_id), "D-1")
    assert d1["outcome"] == "refused" and d1["details"]["missing"] == ["id_test"]


async def test_the_single_roles_stay_single(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    body = set_body(versions, name="two-tests")
    body["roles"].append({**body["roles"][1]})
    refused = await client.post(f"{API}/detector-sets", json=body)
    assert refused.status_code >= 400 and refused.json()["error"]["code"] == "roles_incomplete"


async def test_the_database_allows_many_of_it_and_still_one_training_role(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    body = set_body(versions, name="at-the-table")
    body["roles"] += [extra_role(unshared_extra("one", 41)), extra_role(unshared_extra("two", 42))]
    set_id = await _create(client, body)
    with sync_session_factory()() as db:
        train = db.query(DetectorSetRole).filter_by(set_id=set_id, role="train").one()
        db.add(
            DetectorSetRole(
                id="dsr_second_train",
                set_id=set_id,
                role="train",
                position=0,
                version_id=train.version_id,
                split=train.split,
                input_column="text",
                label_column="label",
                label_mapping={"humorous": "positive"},
                label_source_columns=[],
                negatives_basis=None,
                display_name="",
            )
        )
        with pytest.raises(IntegrityError, match="uq_dw_detector_set_roles_single"):
            db.commit()


# --- D-4 ---------------------------------------------------------------------------------------


def test_the_rule_is_one_function_every_reader_shares() -> None:
    for other in ("id_test", "ood_eval", "calibration_negatives", "extra_eval"):
        assert leakage_counts("extra_eval", other) is False, other
        assert leakage_counts(other, "extra_eval") is False, other
    assert leakage_counts("extra_eval", "train") is True
    assert leakage_counts("train", "extra_eval") is True
    assert leakage_counts("train", "ood_eval") is True
    assert leakage_counts("id_test", "ood_eval") is True


async def _shared_with_held_out(data_dir: Path) -> tuple[dict[str, str], str]:
    """The human-graded view holds copies of held-out rows: rows graded on the merged version reach
    a held-out set legitimately. It holds no training row."""
    versions = humor_versions()
    ood_copy = labelled_rows(120, seed=3, tag="ood", pairs=True)[:30]
    test_copy = labelled_rows(60, seed=2, tag="te")[:20]
    own = labelled_rows(30, seed=40, tag="human")
    extra = view(ood_copy + test_copy + own, "human-eval")
    return versions, extra


async def test_d4_does_not_refuse_rows_shared_with_held_out_or_in_distribution_roles(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions, extra = await _shared_with_held_out(data_dir)
    body = set_body(versions, name="shares-held-out")
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    d4 = outcome(await _check(client, set_id), "D-4")
    assert d4["outcome"] == "green", d4


async def test_d4_does_not_refuse_two_extra_views_that_share_rows(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    rows = labelled_rows(60, seed=40, tag="human")
    body = set_body(versions, name="two-views-one-grading")
    body["roles"] += [
        extra_role(view(rows, "view-a")),
        extra_role(view(rows, "view-b"), display_name="masked left out"),
    ]
    d4 = outcome(await _check(client, await _create(client, body)), "D-4")
    assert d4["outcome"] == "green", d4


async def test_d4_refuses_a_row_shared_with_training_and_names_it(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    copied = labelled_rows(200, seed=1, tag="tr")[:15]
    extra = view(copied + labelled_rows(45, seed=40, tag="human"), "human-eval")
    body = set_body(versions, name="shares-training")
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    d4 = outcome(await _check(client, set_id), "D-4")
    assert d4["outcome"] == "refused"
    crossing = d4["details"]["crossing"]
    assert any("extra evaluation" in k and "training rows" in k for k in crossing), crossing
    assert all("training rows" in k for k in crossing), crossing


async def test_a_real_crossing_between_other_roles_is_still_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """The exemption reaches only the extra view: a held-out set sharing training rows still fails."""
    versions = humor_versions()
    leaked = version(
        {"test": labelled_rows(200, seed=1, tag="tr")[:40] + labelled_rows(80, seed=3, tag="ood")},
        dataset_name="leaked-held-out",
    )
    versions["ood"] = leaked
    extra = view(labelled_rows(80, seed=3, tag="ood"), "human-eval")
    body = set_body(versions, name="held-out-leaks")
    for role in body["roles"]:
        role.pop("pair_column", None)
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    d4 = outcome(await _check(client, set_id), "D-4")
    assert d4["outcome"] == "refused"
    assert not any("extra evaluation" in k for k in d4["details"]["crossing"])
    assert any("out-of-distribution" in k for k in d4["details"]["crossing"])


async def test_the_repair_gives_training_rows_up_and_leaves_the_views_whole(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions = humor_versions()
    copied = labelled_rows(200, seed=1, tag="tr")[:15]
    ood_copy = labelled_rows(120, seed=3, tag="ood", pairs=True)[:10]
    extra = view(copied + ood_copy + labelled_rows(45, seed=40, tag="human"), "human-eval")
    body = set_body(versions, name="repair-me")
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    plan = (await client.post(f"{API}/detector-sets/{set_id}/leakage-repair/plan")).json()
    assert [r["role"] for r in plan["repairs"]] == ["train"], plan
    assert plan["crossing_pairs"] == 15, "the 10 pairs with the held-out set are no crossing here"
    repair = plan["repairs"][0]
    assert repair["rows_to_drop"] == 15
    assert [k["role"] for k in repair["keeps_whole"]] == ["extra_eval"]
    assert "extra_eval" in plan["rule"]


async def test_the_repair_has_nothing_to_do_when_only_exempt_pairs_cross(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    versions, extra = await _shared_with_held_out(data_dir)
    body = set_body(versions, name="nothing-to-repair")
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    plan = (await client.post(f"{API}/detector-sets/{set_id}/leakage-repair/plan")).json()
    assert plan["repairs"] == [] and plan["crossing_pairs"] == 0
    assert "D-4 is green" in plan["message"]


async def test_d11_refuses_a_row_a_kept_view_shares_with_new_training(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """A dry run's set lost 5 of 172 rows to this: a rebuild moved them into training."""
    versions = humor_versions()
    copied = labelled_rows(200, seed=1, tag="tr")[:5]
    extra = view(copied + labelled_rows(55, seed=40, tag="human"), "human-eval")
    body = set_body(versions, name="kept-view")
    body["roles"].append(extra_role(extra))
    d11 = outcome(await _check(client, await _create(client, body)), "D-11")
    assert d11["outcome"] == "refused"
    assert "extra evaluation" in d11["reason"]


async def test_mining_from_the_view_is_mining_from_evaluation_data(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """FR-009.59's refusal reaches the view: rows chosen from a probe's errors on it overlap it."""
    from sqlalchemy import text

    from src.models.version import Version
    from tests.support import db_factories
    from tests.support.calibration_fixtures import make_run

    versions = humor_versions()
    extra = unshared_extra()
    body = set_body(versions, name="mined-from-view")
    body["roles"].append(extra_role(extra))
    set_id = await _create(client, body)
    assert outcome(await _check(client, set_id), "D-4")["outcome"] == "green"
    run = make_run(extra, {})
    with sync_session_factory()() as db:
        _, rev = db_factories.recipe(
            db,
            {
                "format": "midataworks.recipe/v1",
                "steps": [
                    {
                        "operator": "hard_negative_miner",
                        "version": "1",
                        "params": {"probe_label_run_id": run.id, "mode": "near"},
                    }
                ],
            },
        )
        train = db.get(Version, versions["train"])
        assert train is not None
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        train.recipe_hash = rev.recipe_hash
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()
    d4 = outcome(await _check(client, set_id), "D-4")
    assert d4["outcome"] == "refused" and "mined from" in d4["reason"], d4


async def test_d6_does_not_read_the_view_a_generated_view_is_no_expanded_held_out_set(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.core.storage import resolve_under_data_dir
    from src.models.version import Version

    extra = unshared_extra()
    with sync_session_factory()() as db:
        row = db.get(Version, extra)
        assert row is not None
        path = resolve_under_data_dir(row.splits[0]["path"])
    table = pq.read_table(path)
    table = table.set_column(
        table.schema.get_field_index("_dw_origin"),
        "_dw_origin",
        pa.array(["generated"] * table.num_rows, pa.string()),
    )
    pq.write_table(table, path)
    versions = humor_versions()
    body = set_body(versions, name="generated-view")
    body["roles"].append(extra_role(extra))
    d6 = outcome(await _check(client, await _create(client, body)), "D-6")
    assert d6["outcome"] == "green", d6


# --- the send ----------------------------------------------------------------------------------


async def test_a_view_on_the_in_distribution_tests_own_rows_registers_its_own(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Same dataset, same split, same mapping as the set's in-distribution test: the view is a view of
    its own, under its role, not that one's registration reused under another name."""
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    body = set_body(versions)
    id_test = next(r for r in body["roles"] if r["role"] == "id_test")
    body["roles"].append({**id_test, "role": "extra_eval"})
    changed = await client.patch(
        f"{API}/detector-sets/{set_id}",
        json={"roles": body["roles"], "monitored_ref": {"kind": "role", "role_index": 2}},
    )
    assert changed.status_code == 200, changed.text
    again = await start_send(client, set_id)
    assert sender.run(again["job_id"])["status"] == "completed", sender.send(again["send_id"]).error
    regs = sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")
    assert len(regs) == 5, [c.body["name"] for c in regs]
    assert "extra evaluation" in regs[-1].body["name"]
    got = (await client.get(f"{API}/detector-sends/{again['send_id']}")).json()
    first, second = (
        got["run_request"]["eval_dataset_ids"][0],
        got["run_request"]["eval_dataset_ids"][2],
    )
    assert first != second


def test_the_role_table_sends_eval_in_distribution() -> None:
    assert MISTUDIO_ROLE["extra_eval"] == ("eval", "in_distribution")


async def _extra_ready(
    client: httpx.AsyncClient, sender: SendDriver, extra: str
) -> tuple[str, dict[str, str], dict[str, str]]:
    """A set already sent once, then the view added: the operator's actual order of events."""
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    roles = (await client.get(f"{API}/detector-sets/{set_id}")).json()["roles"]
    body = set_body(versions)
    body["roles"].append(extra_role(extra))
    changed = await client.patch(
        f"{API}/detector-sets/{set_id}",
        json={"roles": body["roles"], "monitored_ref": {"kind": "role", "role_index": 2}},
    )
    assert changed.status_code == 200, changed.text
    assert len(changed.json()["roles"]) == len(roles) + 1
    return set_id, versions, repos


async def test_a_re_send_registers_only_the_new_view_and_keeps_the_others(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    extra = unshared_extra()
    set_id, _, _ = await _extra_ready(client, sender, extra)
    before = sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")
    assert len(before) == 4
    pending = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE})
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "builds_pending"
    sender.publisher.run_publish_tasks()
    repo = repos_for({"extra": extra})[extra]
    sender.mistudio.values[(repo, "test")] = {"yes": 26, "no": 25, "excluded": 9}
    started = await start_send(client, set_id)
    result = sender.run(started["job_id"])
    assert result["status"] == "completed", sender.send(started["send_id"]).error

    after = sender.mistudio.calls("POST", "/api/v1/probe-monitors/datasets")
    new = after[len(before) :]
    assert len(new) == 1, [c.body["name"] for c in new]
    view = new[0].body
    assert (view["role"], view["distribution"]) == ("eval", "in_distribution")
    assert view["label_column"] == "human_label"
    assert view["label_mapping"] == dict(sorted(HUMAN.items()))
    assert view["dataset_id"] is not None and "extra evaluation" in view["name"]

    steps = sender.steps(started["send_id"])
    registers = {k: s for (kind, k), s in steps.items() if kind == "register"}
    states = sorted(s.state for s in registers.values())
    assert states == ["done", "reused", "reused", "reused", "reused"]
    downloads = sender.mistudio.calls("POST", "/api/v1/datasets/download")
    assert len(downloads) == 5, "the four earlier splits are not downloaded again"


async def test_the_run_request_lists_the_view_after_the_others(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    extra = unshared_extra()
    set_id, _, _ = await _extra_ready(client, sender, extra)
    pending = await client.post(f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE})
    assert pending.status_code == 409
    sender.publisher.run_publish_tasks()
    repo = repos_for({"extra": extra})[extra]
    sender.mistudio.values[(repo, "test")] = {"yes": 26, "no": 25, "excluded": 9}
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    got = (await client.get(f"{API}/detector-sends/{started['send_id']}")).json()
    evals = got["run_request"]["eval_dataset_ids"]
    assert len(evals) == 3
    views = sender.mistudio.views
    assert [views[v]["distribution"] for v in evals] == [
        "in_distribution",
        "out_of_distribution",
        "in_distribution",
    ]
    assert views[evals[2]]["label_column"] == "human_label"
    extra_id = next(r["id"] for r in got["snapshot"]["roles"] if r["role"] == "extra_eval")
    assert got["mistudio_roles"][extra_id] == {"role": "eval", "distribution": "in_distribution"}


def test_a_published_manifest_never_names_a_role() -> None:
    """Why the vendored dataset-version schema stays byte-identical: its ``detector_role`` enum is
    not extended, and no writer sets the field. miWorkbench and miForge copies need no change."""
    from src.schemas.dataset_version import DetectorRole
    from src.services.detector_sets.role_mapping import ROLE_WORDS

    assert "extra_eval" not in DetectorRole.__args__
    assert set(ROLE_WORDS) == set(MISTUDIO_ROLE)
    assert checks_module.EVALUATION_ROLES == ("id_test", "ood_eval")
