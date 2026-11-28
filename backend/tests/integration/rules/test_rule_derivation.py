"""Rule derivations end to end (FR-016.29 - FR-016.32; FR-016.28's floor; FTID 016 section 7.4).

Real PostgreSQL, the live app, the real task body (run in-process), the production operator
registry for builds. The derivation reads its rows with DuckDB from the version's split files.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.config import get_settings
from src.core.database import get_sync_engine, sync_session_factory
from src.models import Job
from tests.integration.rules.conftest import RS, assert_refusal, live_tools
from tests.integration.rules.test_rule_set_build import ROLES, recipe
from tests.integration.rules.test_rule_set_travel import version_with
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
D = "/api/v1/rule-derivations"
KEEP = [{"operator": "drop_columns", "version": "1", "params": {"columns": ["junk"]}}]


def labelled_rows(n: int = 240, *, keyword: bool = True) -> list[dict[str, Any]]:
    rng = random.Random(7)
    filler = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
    rows = []
    for i in range(n):
        words = rng.sample(filler, 4)
        positive = i % 2 == 0
        if positive and keyword:
            words.insert(rng.randrange(5), "zorbligate")
        rows.append(
            {
                "text": " ".join(words) + f" r{i}",
                "label": "yes" if positive else "no",
                "grp": f"g{i // 2}",
                "junk": 0,
            }
        )
    return rows


async def labelled_version(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, name: str, **kw: Any
) -> str:
    return await version_with(client, driver, data_dir, KEEP, labelled_rows(**kw), name)


def run_jobs() -> list[str]:
    """Run every queued derivation job's real task body in-process (Celery's place)."""
    from src.workers import rule_tasks

    with sync_session_factory()() as db:
        jobs = [
            j.id
            for j in db.query(Job).filter(Job.kind == "rule_derivation", Job.status == "queued")
        ]
    for job_id in jobs:
        rule_tasks.derive_job(job_id)
    return jobs


def body(version_id: str, **over: Any) -> dict[str, Any]:
    return {
        "source_version_id": version_id,
        "text_column": "text",
        "label_column": "label",
        "positive_values": ["yes"],
        "behaviour_key": "fixture-beta",
        "group_column": "grp",
        "min_support": 5,
        "seed": 3,
        **over,
    }


@pytest.fixture(autouse=True)
def small_minimum(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Five held-out rows per class suffice here (the setting in force, read at start). Set on
    the settings object the data_dir fixture patched: an overlay rebuild would drop DATA_DIR."""
    monkeypatch.setattr(get_settings(), "rule_derivation_min_held_out_per_class", 5)


async def derive(client: httpx.AsyncClient, version_id: str, **over: Any) -> dict[str, Any]:
    started = await client.post(D, json=body(version_id, **over))
    assert started.status_code == 202, started.text
    assert started.json()["next"]["tool"] in await live_tools()
    assert run_jobs() == [started.json()["job_id"]]
    got = await client.get(f"{D}/{started.json()['derivation_id']}")
    assert got.status_code == 200, got.text
    out: dict[str, Any] = got.json()
    return out


async def test_a_derivation_finds_the_keyword_and_saves_only_when_asked(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "kw")
    out = await derive(client, version_id)
    assert out["status"] == "completed" and out["state"] == "measured", out
    assert out["result"]["literals"][0]["literal"] == "zorbligate"
    assert out["result"]["draft"]["precision"] == 1.0
    assert out["result"]["groups"]["on_both_sides"] == 0
    assert out["params"]["min_held_out_per_class"] == 5, "the setting in force is kept"
    assert out["next"]["tool"] == "dataworks_save_rule_set"
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_rule_sets")).scalar() == 0
    saved = await client.post(RS, json={"document": out["draft"]})
    assert saved.status_code == 201, saved.text
    got = (await client.get(f"{RS}/{out['draft']['name']}")).json()
    assert got["document"]["provenance"]["source_version_id"] == version_id
    assert got["document"]["provenance"]["derivation_id"] == out["id"]


async def test_one_seed_reproduces_the_draft_hash(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "repro")
    a = await derive(client, version_id, name="repro-a")
    b = await derive(client, version_id, name="repro-a")
    # the stored draft names its derivation (provenance), so the reproducible hash is the rules'
    assert a["result"]["rules_sha256"] == b["result"]["rules_sha256"] is not None
    assert a["draft_sha256"] != b["draft_sha256"], "each draft names its own derivation"
    c = await derive(client, version_id, name="repro-a", seed=11)
    assert c["result"]["seed"] == 11


async def test_no_rule_reads_low_coverage_and_too_few_rows_not_measured(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = await labelled_version(client, production_driver, data_dir, "plain", keyword=False)
    low = await derive(client, plain)
    assert low["state"] == "low_coverage", low
    monkeypatch.setattr(get_settings(), "rule_derivation_min_held_out_per_class", 1000)
    few = await derive(client, plain)
    assert few["state"] == "not_measured"
    assert set(few["result"]["draft"].values()) == {None}


async def test_refusals_name_their_column_and_the_cap(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "refuse")
    missing = await client.post(D, json=body(version_id, label_column="nope"))
    await assert_refusal(missing, 422, "label_column_missing", column="nope")
    not_binary = await client.post(D, json=body(version_id, label_column="text"))
    await assert_refusal(not_binary, 422, "label_column_not_binary", column="text")
    wrong_positive = await client.post(D, json=body(version_id, positive_values=["maybe"]))
    await assert_refusal(wrong_positive, 422, "label_column_not_binary", column="label")
    monkeypatch.setattr(get_settings(), "rule_derivation_max_signals", 2)
    capped = await client.post(D, json=body(version_id, max_signals=3))
    assert capped.status_code == 413
    assert capped.json()["error"]["details"]["setting"] == "RULE_DERIVATION_MAX_SIGNALS"
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_rule_derivations")).scalar() == 0


async def test_a_cancelled_job_cancels_its_derivation_and_a_failure_stores_its_code(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "cancel")
    started = (await client.post(D, json=body(version_id))).json()
    from src.core.cancellation import CancelCheck, OperatorCancelled

    def cancel_now(self: CancelCheck, detail: str = "") -> None:
        raise OperatorCancelled(self.job_id, "cancelled", detail)

    monkeypatch.setattr(CancelCheck, "raise_if_cancelled", cancel_now)
    run_jobs()
    out = (await client.get(f"{D}/{started['derivation_id']}")).json()
    assert out["status"] == "cancelled", out
    with get_sync_engine().connect() as conn:  # the stored row, not only the read-time view
        stored = conn.execute(
            text("SELECT status FROM dw_rule_derivations WHERE id = :i"),
            {"i": started["derivation_id"]},
        ).scalar_one()
    assert stored == "cancelled"
    monkeypatch.setattr(CancelCheck, "raise_if_cancelled", lambda self, detail="": None)
    from src.services.rules import derivation_service

    def broken(*a: Any, **k: Any) -> Any:
        raise RuntimeError("the reader broke")

    monkeypatch.setattr(derivation_service, "read_rows", broken)
    started = (await client.post(D, json=body(version_id))).json()
    run_jobs()
    out = (await client.get(f"{D}/{started['derivation_id']}")).json()
    assert out["status"] == "failed" and out["error"]["code"] == "derivation_failed", out
    with sync_session_factory()() as db:
        job = db.get(Job, started["job_id"])
        assert job is not None and job.status == "failed"
        assert job.result is not None and job.result["error"]["code"] == "derivation_failed"


async def test_a_terminal_derivation_never_changes(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    from sqlalchemy.exc import DBAPIError

    version_id = await labelled_version(client, production_driver, data_dir, "frozen")
    out = await derive(client, version_id)
    with pytest.raises(DBAPIError, match="terminal"), get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_rule_derivations SET state = 'low_coverage' WHERE id = :i"),
            {"i": out["id"]},
        )
    with pytest.raises(DBAPIError, match="purge"), get_sync_engine().begin() as conn:
        conn.execute(text("DELETE FROM dw_rule_derivations WHERE id = :i"), {"i": out["id"]})


async def test_provenance_that_disagrees_with_its_derivation_is_refused(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "prov")
    out = await derive(client, version_id)
    document = json.loads(json.dumps(out["draft"]))
    document["provenance"]["seed"] = 4
    refused = await client.post(RS, json={"document": document})
    await assert_refusal(refused, 422, "provenance_mismatch", fields=["seed"])
    # Review R1 (slices 2-4): the field the floor reads. A provenance naming another (say a
    # public) version as the source would carry that version's floor instead of the real one.
    other = await labelled_version(client, production_driver, data_dir, "prov-other")
    forged = json.loads(json.dumps(out["draft"]))
    forged["provenance"]["source_version_id"] = other
    refused = await client.post(RS, json={"document": forged})
    await assert_refusal(refused, 422, "provenance_mismatch", fields=["source_version_id"])


# --- the floor and the ancestor (FR-016.31, FR-016.28; FTID I6) ---------------------------------


async def test_a_version_built_with_a_derived_set_inherits_its_source_and_its_floor(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
) -> None:
    # the source of the labels is private_only (licence "other")
    rows = labelled_rows()
    source = make_source(data_dir, {"train": rows}, repo_id="t/private-labels", licence="other")
    ds, rev = await setup(client, recipe(*KEEP), name="private-labels")
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    job_id = response.json()["job_id"]
    assert production_driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        labels_version = str(job.result["version_id"])
    out = await derive(client, labels_version)
    saved = (await client.post(RS, json={"document": out["draft"]})).json()
    # a fresh, permissive version screened with the derived set
    built = await version_with(
        client,
        production_driver,
        data_dir,
        [
            {
                "operator": "rule_set",
                "version": "1",
                "params": {
                    "rule_set_name": saved["name"],
                    "rule_set_version": saved["version"],
                    "rule_set_sha256": saved["sha256"],
                    "use": "screen",
                    "text_column": "text",
                },
            }
        ],
        [{"text": "zorbligate here"}, {"text": "nothing"}],
        "screened",
    )
    with get_sync_engine().connect() as conn:
        inputs = conn.execute(
            text(
                "SELECT kind, CAST(input_version_id AS text), CAST(source_id AS text) "
                "FROM dw_version_inputs WHERE version_id = CAST(:v AS uuid) ORDER BY position"
            ),
            {"v": built},
        ).all()
        manifest = json.loads(
            conn.execute(
                text(
                    "SELECT convert_from(manifest, 'UTF8') FROM dw_versions WHERE id = CAST(:v AS uuid)"
                ),
                {"v": built},
            ).scalar_one()
        )
    assert [i[0] for i in inputs] == ["source", "rule_source"]
    assert inputs[1][1] == labels_version
    assert [i["kind"] for i in manifest["inputs"]] == ["source"], "inputs: what the build read"
    assert source in {s["source_id"] for s in manifest["sources"]}, "sources: who taught it"
    version = (await client.get(f"/api/v1/versions/{built}")).json()
    assert [i["kind"] for i in version["inputs"]] == ["source"]
    lineage = (await client.get(f"/api/v1/versions/{built}/lineage")).json()
    assert lineage["rule_sources"] == [{"version_id": labels_version, "number": 1}]
    parent = (await client.get(f"/api/v1/versions/{labels_version}/lineage")).json()
    assert {"version_id": built, "relation": "taught_rules_to"}.items() <= next(
        c for c in parent["children"] if c["version_id"] == built
    ).items()
    # the readers that walk DATA ancestors do not follow a rule_source (FTID I6, 20.5)
    from src.core.database import async_session_factory
    from src.services import lineage_service
    from src.services.generation import independence

    async with async_session_factory()() as adb:
        from src.models import Version as V

        row_async = await adb.get(V, built)
        assert row_async is not None
        walked = {e.version_id for e in await lineage_service.lineage_executions(adb, row_async)}
    assert walked == {built}, walked
    with sync_session_factory()() as db:
        visited: list[str] = []
        real_get = db.get

        def spy(model: Any, ident: Any, **kw: Any) -> Any:
            visited.append(str(ident))
            return real_get(model, ident, **kw)

        db.get = spy  # type: ignore[method-assign]
        independence.bound_generation_runs(db, built)
    assert labels_version not in visited and built in visited
    # C-1 sees the private source through the ancestor: a public push is refused, private allowed
    from src.models import Version
    from src.services.publishing.check_inputs import assemble
    from src.services.publishing.checks import evaluate_checks, push_allowed

    with sync_session_factory()() as db:
        row = db.get(Version, built)
        assert row is not None
        assembled = assemble(db, row, label_column=None, visibility="public", token_scope="write")
        outcomes = evaluate_checks(assembled.inputs)
    assert assembled.lineage["rule_sources"] == [{"version_id": labels_version, "number": 1}]
    from src.services.publishing.card import _rule_source_lines

    lines = _rule_source_lines({"extensions": {"lineage": assembled.lineage}})
    assert "their labels taught its rules" in lines[-1] and labels_version in lines[-1]
    c1 = next(o for o in outcomes if o.check == "C-1")
    assert c1.outcome.value == "amber" and source in c1.evidence["sources"], c1
    assert push_allowed(outcomes, "public")[0] is False
    assert all(o.check != "C-1" for o in push_allowed(outcomes, "private")[1])
    # D40: the labels dataset cannot be purged while a version built with its derived rules (the
    # rule_source input) or the saved rule set naming its derivation remains; each is named.
    labels_dataset = _dataset_of(labels_version)
    assert (await client.post(f"/api/v1/datasets/{labels_dataset}/archive")).status_code == 200
    refused = await client.post(f"{PURGE}/dataset/{labels_dataset}", json={"reason": "test"})
    assert refused.status_code == 409, refused.text
    blockers = refused.json()["error"]["details"]["blockers"]
    taught = next(b for b in blockers if b["table"] == "dw_version_inputs")
    assert taught["code"] == "rule_source" and taught["ids"] == [built], taught
    assert "rule_source" in taught["message"] and "screened v1" in taught["message"], taught
    naming = next(b for b in blockers if b["table"] == "dw_config_versions")
    assert naming["code"] == "rule_source" and naming["ids"] == [out["id"]], naming


async def test_a_boolean_label_column_derives_like_a_string_one(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """Review R1 (slices 2-4): start compared labels with ``str`` (``True``) and the run with
    DuckDB's text (``true``), so a boolean label passed start and then matched no positive row:
    a silent all-negative derivation. Both now key booleans in lower case."""
    rows = [{**r, "label": r["label"] == "yes"} for r in labelled_rows()]
    version_id = await version_with(client, production_driver, data_dir, KEEP, rows, "boolean")
    out = await derive(client, version_id, positive_values=[True], name="boolean-rules")
    assert out["status"] == "completed" and out["state"] == "measured", out
    assert out["result"]["literals"][0]["literal"] == "zorbligate"
    assert out["result"]["draft"]["precision"] == 1.0
    # the value as JSON text names the same class (start used to refuse it: "True" != "true")
    as_text = await derive(client, version_id, positive_values=["true"], name="boolean-rules")
    assert as_text["state"] == "measured", as_text
    assert as_text["result"]["rules_sha256"] == out["result"]["rules_sha256"]


async def test_positive_values_naming_both_classes_are_refused(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """Review R1 (slices 2-4): positives covering both classes made every row positive."""
    version_id = await labelled_version(client, production_driver, data_dir, "both")
    both = await client.post(D, json=body(version_id, positive_values=["yes", "no"]))
    await assert_refusal(both, 422, "label_column_not_binary", column="label")
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_rule_derivations")).scalar() == 0


PURGE = "/api/v1/purge"


def _dataset_of(version_id: str) -> str:
    with get_sync_engine().connect() as conn:
        return str(
            conn.execute(
                text("SELECT dataset_id FROM dw_versions WHERE id = CAST(:v AS uuid)"),
                {"v": version_id},
            ).scalar_one()
        )


def _derivations(version_id: str) -> list[str]:
    with get_sync_engine().connect() as conn:
        return sorted(
            str(r)
            for r in conn.execute(
                text(
                    "SELECT id FROM dw_rule_derivations WHERE source_version_id = CAST(:v AS uuid)"
                ),
                {"v": version_id},
            ).scalars()
        )


async def test_a_purged_dataset_takes_its_derivations_but_not_while_a_rule_set_names_one(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """D40 (operator 2026-10-23; 016 Review R1 O1). Before it, a dataset whose version was ever a
    derivation source could never be purged (blocker "rule derivation", no delete route). Now
    its derivations go with it, but never while a saved rule set's provenance names one: that
    rule set's rule_source would name a deleted derivation."""
    version_id = await labelled_version(client, production_driver, data_dir, "purge-src")
    kept = await derive(client, version_id, name="purge-kept")
    loose = await derive(client, version_id, name="purge-loose")
    assert _derivations(version_id) == sorted([kept["id"], loose["id"]])
    assert (await client.post(RS, json={"document": kept["draft"]})).status_code == 201
    dataset_id = _dataset_of(version_id)
    assert (await client.post(f"/api/v1/datasets/{dataset_id}/archive")).status_code == 200

    refused = await client.post(f"{PURGE}/dataset/{dataset_id}", json={"reason": "test"})
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "purge_blocked"
    (blocker,) = error["details"]["blockers"]
    assert blocker["code"] == "rule_source" and blocker["table"] == "dw_config_versions"
    assert blocker["ids"] == [kept["id"]], "names the derivation the rule set's provenance names"
    assert "purge-kept v1" in blocker["message"] and kept["id"] in blocker["message"]
    assert _derivations(version_id) == sorted([kept["id"], loose["id"]]), "nothing deleted"

    # the rule set goes first (archived, unused): then the dataset purges with BOTH derivations
    assert (await client.post(f"{RS}/purge-kept/archive")).status_code == 200
    gone = await client.post(f"{PURGE}/rule_set/purge-kept", json={"reason": "test"})
    assert gone.status_code == 200, gone.text
    plan = await client.post(
        f"{PURGE}/plan", json={"items": [{"kind": "dataset", "id": dataset_id}]}
    )
    planned = plan.json()["items"][0]
    assert planned["status"] == "purgeable", planned
    assert planned["counts"]["rule_derivations"] == 2
    assert _derivations(version_id) == sorted([kept["id"], loose["id"]]), "a plan deletes nothing"
    purged = await client.post(f"{PURGE}/dataset/{dataset_id}", json={"reason": "test"})
    assert purged.status_code == 200, purged.text
    assert purged.json()["counts"] == planned["counts"]
    assert purged.json()["counts"]["rule_derivations"] == 2
    with get_sync_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM dw_rule_derivations")).scalar() == 0
        jobs = conn.execute(
            text("SELECT count(*) FROM dw_jobs WHERE id = ANY(CAST(:j AS text[]))"),
            {"j": [kept["job_id"], loose["job_id"]]},
        ).scalar()
    assert jobs == 0, "the derivation jobs nothing else records go too"


async def _delete_version(client: httpx.AsyncClient, version_id: str) -> httpx.Response:
    return await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "superseded"}
    )


async def test_a_live_derivation_blocks_its_source_delete_and_a_dead_job_does_not(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """The 016 delete checker (full-run fix 6), driven through the route. Review R1 (slices
    2-4): a derivation whose job died before writing the row (janitor-failed, or cancelled before
    a worker claimed it) left the row ``queued``/``running`` for ever, and the checker, reading the
    row alone, refused the delete for ever with "cancel its job" - a job already ended."""
    version_id = await labelled_version(client, production_driver, data_dir, "delete")
    started = (await client.post(D, json=body(version_id))).json()
    refused = await _delete_version(client, version_id)
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "version_in_use"
    assert error["details"] == {"derivation_id": started["derivation_id"]}
    with get_sync_engine().begin() as conn:  # the janitor's end: the job only
        conn.execute(
            text("UPDATE dw_jobs SET status = 'failed' WHERE id = :j"), {"j": started["job_id"]}
        )
        stored = conn.execute(
            text("SELECT status FROM dw_rule_derivations WHERE id = :i"),
            {"i": started["derivation_id"]},
        ).scalar_one()
    assert stored == "queued", "the row never heard of the job's end"
    deleted = await _delete_version(client, version_id)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["state"] == "deleted"


async def test_a_finished_derivation_does_not_block_its_source_delete(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    version_id = await labelled_version(client, production_driver, data_dir, "finished")
    out = await derive(client, version_id)
    assert out["status"] == "completed"
    deleted = await _delete_version(client, version_id)
    assert deleted.status_code == 200, deleted.text


async def test_a_clone_of_a_derived_set_keeps_its_provenance(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """Review R1 (slices 2-4): ``/provenance`` was substitutable, so one clone call made a copy of
    a derived rule set with no source and no floor. A clone keeps it; substituting it is refused."""
    version_id = await labelled_version(client, production_driver, data_dir, "clone")
    out = await derive(client, version_id, name="derived-to-clone")
    assert (await client.post(RS, json={"document": out["draft"]})).status_code == 201
    for pointer, value in (("/provenance", None), ("/provenance/source_version_id", "x")):
        refused = await client.post(
            f"{RS}/derived-to-clone/clone",
            json={
                "new_name": "laundered",
                "new_behaviour_key": "fixture-beta",
                "substitutions": [{"pointer": pointer, "value": value}],
            },
        )
        await assert_refusal(refused, 422, "rule_set_invalid", pointer=pointer)
    kept = await client.post(
        f"{RS}/derived-to-clone/clone",
        json={"new_name": "kept-copy", "new_behaviour_key": "fixture-beta"},
    )
    assert kept.status_code == 201, kept.text
    got = (await client.get(f"{RS}/kept-copy")).json()
    assert got["document"]["provenance"]["source_version_id"] == version_id


async def test_the_overlap_note_counts_source_rows_in_held_out_splits_only(
    client: httpx.AsyncClient, production_driver: RealDriver, data_dir: Path
) -> None:
    """Review R1 (slices 2-4): ``carried_rule_set_facts`` (C-8's input and the overlap note's
    count) was pinned only by unit tests that construct the facts. Here the real function reads a
    version re-split from the very version the rule set was learned from: every held-out row is a
    source row, and train rows are not counted."""
    labels_version = await labelled_version(client, production_driver, data_dir, "overlap")
    out = await derive(client, labels_version, name="overlap-rules")
    saved = (await client.post(RS, json={"document": out["draft"]})).json()
    step = {
        "operator": "rule_set",
        "version": "1",
        "params": {
            "rule_set_name": saved["name"],
            "rule_set_version": saved["version"],
            "rule_set_sha256": saved["sha256"],
            "use": "screen",
            "text_column": "text",
        },
    }
    split = {
        "operator": "split",
        "version": "1.0.0",
        "params": {
            "split_names": ["train", "test"],
            "split_fractions": [0.5, 0.5],
            "held_out": ["test"],
            "group_column": "grp",
        },
    }
    with sync_session_factory()() as db:
        from src.models import Version

        source_row = db.get(Version, labels_version)
        assert source_row is not None
        dataset_id = str(source_row.dataset_id)
    created = await client.post(
        "/api/v1/recipes", json={"name": "overlap-resplit", "body": recipe(step, split)}
    )
    assert created.status_code == 201, created.text
    response = await request(
        client,
        dataset_id,
        created.json()["head_revision_id"],
        [{"kind": "version", "version_id": labels_version}],
        seed=5,
    )
    assert response.status_code == 202, response.text
    assert production_driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        built = str(job.result["version_id"])
    import pyarrow.parquet as pq

    from src.core.storage import resolve_under_data_dir
    from src.models import Version
    from src.services.publishing.check_inputs import assemble, carried_rule_set_facts
    from src.services.publishing.checks import evaluate_checks

    with sync_session_factory()() as db:
        row = db.get(Version, built)
        assert row is not None
        held = [s for s in row.splits if s["held_out"]]
        kept = [s for s in row.splits if not s["held_out"]]
        assert held and kept
        held_rows = sum(pq.read_metadata(resolve_under_data_dir(s["path"])).num_rows for s in held)
        assert 0 < held_rows < 240
        facts = carried_rule_set_facts(db, row)
        assert facts is not None and len(facts) == 1
        assert facts[0].derived_from == labels_version
        assert facts[0].overlap_rows == held_rows
        assembled = assemble(db, row, label_column=None, visibility="private", token_scope="write")
        outcomes = evaluate_checks(assembled.inputs)
    note = next(o for o in outcomes if o.check == "N-rule_source_overlap")
    assert note.evidence["overlap_rows"] == held_rows


async def test_a_rule_set_learned_from_a_private_only_version_carries_its_floor(
    client: httpx.AsyncClient,
    production_driver: RealDriver,
    data_dir: Path,
) -> None:
    """Review R4 (017 FR-017.48): a derived rule set's literals are words of the rows it was
    learned from, and it travels with every version built with it. A version screened with a set
    learned from a ``private_only`` version is ``private_only`` too, through its ``rule_source``
    ancestor, though it reads none of that version's rows."""
    source = make_source(data_dir, {"train": labelled_rows()}, repo_id="t/floored-labels")
    floored = {**recipe(*KEEP), "visibility_floor": "private_only"}
    ds, rev = await setup(client, floored, name="floored-labels")
    response = await request(client, ds, rev, [src(source)], seed=7, column_roles=ROLES)
    job_id = response.json()["job_id"]
    assert production_driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        labels_version = str(job.result["version_id"])
    out = await derive(client, labels_version)
    saved = (await client.post(RS, json={"document": out["draft"]})).json()
    built = await version_with(
        client,
        production_driver,
        data_dir,
        [
            {
                "operator": "rule_set",
                "version": "1",
                "params": {
                    "rule_set_name": saved["name"],
                    "rule_set_version": saved["version"],
                    "rule_set_sha256": saved["sha256"],
                    "use": "screen",
                    "text_column": "text",
                },
            }
        ],
        [{"text": "zorbligate here"}, {"text": "nothing"}],
        "screened-floored",
    )
    with get_sync_engine().connect() as conn:
        floors = dict(
            conn.execute(
                text(
                    "SELECT CAST(id AS text), visibility_floor FROM dw_versions "
                    "WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": labels_version, "b": built},
            ).all()
        )
    assert floors == {labels_version: "private_only", built: "private_only"}
