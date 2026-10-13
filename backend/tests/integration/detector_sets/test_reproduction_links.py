"""Reproduction links: an imported probe reproduces against an evaluation miStudio already recorded
(009 FR-009.77, option (b) of the operator decision of 2026-10-07).

The fake miStudio serves the REAL bodies captured read-only from production on 2026-10-07
(``report_pm_c99519a98e08.json``, ``probe_datasets_2026-10-07.json``, ``run_pmr_a1993af56691.json``,
``samples_humicroedit_eval_page1.json``'s row shape), with ONE scripted change so the gate stays
small: the in-distribution view ``pmd_fb14b0b206a5`` is described as 60 rows (30/30) instead of
1,090. Its recorded AUROC 0.9751 [0.9652, 0.9837] is miStudio's own. ``gate_score`` (shared with
``test_probe_verdict_runs``) gives miLLM an AUROC of 880/900 = 0.9778 on those 60 rows: inside.

What is not faked: 009's link service and checks, 005's plan, start and worker, the gate, the probe
client. miLLM's HTTP is ``fake_millm``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import select, text

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.reproduction_link import ReproductionLink
from src.services.detector_sets import reproduction
from tests.integration.detector_sets.test_probe_verdict_runs import (
    LLAMA,
    MISTUDIO_PROBE,
    body,
    gate_score,
    millm,  # noqa: F401
    perfect,
    plan,
    refreshed,
    rows,
)
from tests.support import db_factories
from tests.support.fake_millm import ORIGIN, probe_row
from tests.support.fake_mistudio import BASE, FIXTURES, FakeMiStudio
from tests.support.labeling_fixtures import Labeling, labeling, make_version, row_key  # noqa: F401
from tests.support.send_fixtures import API

SOURCE = "pm_c99519a98e08"  # pr_25c6d3881f6d's source in production
PROBE = "pr_humor"
VIEW = "pmd_fb14b0b206a5"  # Humor (JEV-9B) held-out test, in-distribution
OOD_VIEW = "pmd_c99c8595671d"  # Humicroedit humor (human labels), out-of-distribution
RUN = "pmr_a1993af56691"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def gate_texts() -> list[tuple[str, str]]:
    """The 60 evaluated rows: ``te {i} ...``, odd i humorous (``gate_score``'s convention)."""
    return [(f"te {i} a headline", "humorous" if i % 2 == 1 else "not_humorous") for i in range(60)]


def served_row(i: int, txt: str, label: str) -> dict[str, Any]:
    """One row in the shape miStudio's samples route served on 2026-10-07."""
    page = json.loads((FIXTURES / "samples_humicroedit_eval_page1.json").read_text())
    shape = copy.deepcopy(page["data"][0]["data"])
    shape.update({"id": f"hum-e-{i}", "text": txt, "label": label})
    return shape


def real_mistudio(n_view: int = 60) -> FakeMiStudio:
    fake = FakeMiStudio()
    report = json.loads((FIXTURES / "report_pm_c99519a98e08.json").read_text())
    for e in report["evaluations"]:
        if e["dataset_id"] == VIEW:
            e["n_positive"] = e["metrics"]["n_positive"] = n_view // 2
            e["n_negative"] = e["metrics"]["n_negative"] = n_view // 2
    fake.reports[SOURCE] = report
    for v in json.loads((FIXTURES / "probe_datasets_2026-10-07.json").read_text()):
        if v["id"] == VIEW:
            v["counts"] = {
                "kinds": {"plain": n_view},
                "positive": n_view // 2,
                "negative": n_view // 2,
                "excluded": 0,
                "filtered_out": 0,
                "unparseable": 0,
            }
        fake.views[v["id"]] = v
    fake.run_rows[RUN] = json.loads((FIXTURES / f"run_{RUN}.json").read_text())
    dataset = fake.views[VIEW]["dataset_id"]
    fake.samples[dataset] = [served_row(i, t, lab) for i, (t, lab) in enumerate(gate_texts())]
    return fake


@pytest.fixture
def studio(monkeypatch: pytest.MonkeyPatch) -> FakeMiStudio:
    from src.clients import mistudio_client

    fake = real_mistudio()
    monkeypatch.setattr(mistudio_client, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    return fake


@pytest.fixture
def imported(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> Labeling:  # noqa: F811
    """miLLM holds an IMPORTED probe whose source miStudio probe no send ever described."""
    labeling.millm.resident = dict(LLAMA)
    labeling.millm.probes = {
        PROBE: probe_row(PROBE, hf_id=LLAMA["repo_id"], mistudio_probe_id=SOURCE)
    }
    labeling.millm.probe_score = gate_score
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    return labeling


def labelled_version(
    data_dir: Path, pairs: list[tuple[str, str]], *, split: str = "test", extra: Any = None
) -> str:
    """A completed version whose ``split`` holds ``text`` and ``label`` columns."""
    version_id = db_factories.uid()
    relative = f"versions/{version_id}/{split}.parquet"
    path = data_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    texts = [t for t, _ in pairs]
    pq.write_table(
        pa.table(
            {
                "_dw_row_key": [row_key(t) for t in texts],
                "_dw_occurrence": [0] * len(texts),
                "_dw_origin": ["source"] * len(texts),
                "_dw_split": [split] * len(texts),
                "text": texts,
                "label": [lab for _, lab in pairs],
            }
        ),
        path,
    )
    with sync_session_factory()() as db:
        db_factories.version(
            db,
            id=version_id,
            splits=[
                {
                    "name": split,
                    "path": relative,
                    "rows": len(texts),
                    "held_out": False,
                    "bytes": path.stat().st_size,
                    "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "logical_digest": hashlib.sha256(b"link-fixture").hexdigest(),
                }
            ],
            total_rows=len(texts),
            column_roles={"text": "content", "label": "metadata"},
        )
        db.commit()
    return version_id


def link_body(version_id: str, **over: Any) -> dict[str, Any]:
    out = {
        "mistudio_probe_id": SOURCE,
        "probe_dataset_id": VIEW,
        "version_id": version_id,
        "split": "test",
    }
    out.update(over)
    return out


def links() -> list[ReproductionLink]:
    with sync_session_factory()() as db:
        found = list(db.execute(select(ReproductionLink)).scalars())
        for r in found:
            db.expunge(r)
        return found


# --- the whole way through ------------------------------------------------------------------------


async def test_an_imported_probe_reproduces_through_a_linked_evaluation(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,
    studio: FakeMiStudio,
) -> None:
    to_label = make_version(data_dir, rows(10))

    # 1. No snapshot can describe this probe: the plan refuses, and the refusal now carries the
    #    probe, the one-input preflight against the bar, the state, BOTH ways through and the
    #    evaluations a link could name.
    refused = await plan(client, body(to_label))
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "REPRODUCTION_UNAVAILABLE"
    assert "(a) send the detector set" in error["message"]
    assert "(b) for a probe miDataworks never sent" in error["message"]
    details = error["details"]
    assert details["probe"]["probe_id"] == PROBE and details["probe"]["mistudio_probe_id"] == SOURCE
    preflight = details["probe"]["preflight"]
    assert preflight["checked"] is True
    # the sampled row scores 3.0 against the fake's bar of 2.5
    assert preflight["score"] == 3.0 and preflight["threshold"] == 2.5
    assert preflight["verdict"] is True
    assert details["reproduction"]["state"] == "unavailable"
    assert [w["way"] for w in details["reproduction"]["ways"]] == [
        "send_and_train",
        "link_existing_evaluation",
    ]
    candidates = {c["probe_dataset_id"]: c for c in details["link_candidates"]["items"]}
    assert set(candidates) == {VIEW, OOD_VIEW}
    assert candidates[OOD_VIEW]["auroc"] == pytest.approx(0.7375442903756163)
    assert candidates[VIEW]["distribution"] == "in_distribution"

    # 2. Link the version split holding the evaluated rows: every check runs and passes.
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    link = made.json()
    assert link["check_level"] == "content" and link["role"] == "id_test"
    checks = link["checks"]
    assert checks["row_count"] == {"ran": True, "ours": 60, "mistudio": 60, "passed": True}
    assert checks["class_balance"]["passed"] is True
    content = checks["content"]
    assert content["ran"] is True and content["ordered_match"] is True and content["passed"]
    assert content["ours_sha256"] == content["mistudio_sha256"]
    assert link["mistudio_auroc"] == pytest.approx(0.9750930056392559)
    assert link["mistudio_ci"] == pytest.approx([0.965238616278091, 0.9836579412507365])
    # pm_c99519a98e08 predates miStudio's render_form (absent in this 2026-10-07 report): rendered
    # without the generation prompt, which miLLM adds to one user turn - so the forms DIFFER.
    assert link["scoring_form"]["agreement"] == "differs"
    assert link["scoring_form"]["mistudio"]["render_form_recorded"] is False
    assert link["scoring_form"]["token_ids_compared"] is False
    assert link["scoring_form"]["mistudio"]["scope"] == "all"
    assert link["scoring_form"]["mistudio"]["template_hash"].startswith("e10ca381")
    assert link["mistudio_run_id"] == RUN and link["created_by"] == operator_name
    served = studio.calls("GET", "/api/v1/datasets/")
    assert len(served) == 1 and served[0].query == {"page": "1", "limit": "100"}

    # 3. The plan now has a target, tagged as the link.
    planned = await plan(client, body(to_label))
    assert planned.status_code == 200, planned.text
    gate = planned.json()["reproduction"]
    assert gate["state"] == "will_run" and gate["source"] == "linked_mistudio_evaluation"
    assert gate["link_id"] == link["id"] and gate["link_check_level"] == "content"
    assert gate["snapshot_id"] is None and gate["n_rows"] == 60
    assert gate["scoring_form"] == link["scoring_form"]

    # 4. The run reproduces miStudio's AUROC on the linked rows, then labels.
    started = await client.post(f"{API}/label-runs", json=body(to_label))
    assert started.status_code == 201, started.text
    final = imported.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    record = final.endpoint_snapshot["reproduction"]
    assert record["state"] == "passed" and record["source"] == "linked_mistudio_evaluation"
    assert record["link_id"] == link["id"] and record["rows_scored"] == 60
    assert record["millm_auroc"] == pytest.approx(880 / 900, abs=1e-6)

    # 5. A used link is immutable evidence.
    gone = await client.delete(f"{API}/reproduction-links/{link['id']}")
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "link_in_use"
    assert final.id in gone.json()["error"]["details"]["label_run_ids"]
    got = (await client.get(f"{API}/reproduction-links/{link['id']}")).json()
    assert got["used_by_label_runs"] == [final.id]
    listed = (
        await client.get(f"{API}/reproduction-links", params={"mistudio_probe_id": SOURCE})
    ).json()
    assert [i["id"] for i in listed["items"]] == [link["id"]]
    with sync_session_factory()() as db, pytest.raises(Exception, match="immutable evidence"):
        db.execute(text("DELETE FROM dw_reproduction_links WHERE id = :i"), {"i": link["id"]})
    with sync_session_factory()() as db, pytest.raises(Exception, match="append-only"):
        db.execute(
            text("UPDATE dw_reproduction_links SET auroc = 0.5 WHERE id = :i"), {"i": link["id"]}
        )


async def test_a_linked_gate_that_does_not_reproduce_names_both_figures_and_the_link(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,
    studio: FakeMiStudio,
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    link = (await client.post(f"{API}/reproduction-links", json=link_body(version_id))).json()
    imported.millm.probe_score = perfect  # AUROC 1.0, above miStudio's 0.9837
    started = await client.post(f"{API}/label-runs", json=body(make_version(data_dir, rows(4))))
    assert started.status_code == 201, started.text
    final = imported.run_until_done(started.json()["id"])
    assert final.state == "failed" and final.error["code"] == "REPRODUCTION_FAILED"
    message = final.error["message"]
    assert "1.0000" in message and "0.9751" in message and "[0.9652, 0.9837]" in message
    assert link["id"] in message and "content hash" in message


# --- the checks ------------------------------------------------------------------------------------


async def test_a_split_whose_row_count_differs_is_refused_and_nothing_is_stored(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    version_id = labelled_version(data_dir, gate_texts()[:59])
    refused = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "link_rows_differ"
    assert "59 rows of the split map to a class, and miStudio evaluated 60" in error["message"]
    assert error["details"]["checks"]["row_count"]["passed"] is False
    assert links() == []


async def test_a_split_whose_class_balance_differs_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    pairs = gate_texts()
    pairs[0] = (pairs[0][0], "humorous")  # 31 positive, 29 negative: same row count
    version_id = labelled_version(data_dir, pairs)
    refused = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert refused.status_code == 409, refused.text
    checks = refused.json()["error"]["details"]["checks"]
    assert checks["row_count"]["passed"] is True and checks["class_balance"]["passed"] is False
    assert links() == []


async def test_same_counts_with_different_text_is_refused_by_the_content_hash(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    pairs = gate_texts()
    pairs[7] = ("te 7 a different headline", pairs[7][1])
    version_id = labelled_version(data_dir, pairs)
    refused = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    checks = error["details"]["checks"]
    assert checks["row_count"]["passed"] and checks["class_balance"]["passed"]
    assert checks["content"]["ran"] is True and checks["content"]["passed"] is False
    assert "hash differently" in error["message"]
    assert links() == []


async def test_the_same_rows_in_another_order_link_on_content(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    version_id = labelled_version(data_dir, list(reversed(gate_texts())))
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    content = made.json()["checks"]["content"]
    assert content["ordered_match"] is False and content["unordered_match"] is True
    assert made.json()["check_level"] == "content"


async def test_rows_the_samples_route_cannot_tie_to_the_view_link_counts_only(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    """miStudio's samples route serves a multi-split dataset's TRAIN split and does not say so:
    rows that do not reproduce the view's own counts are not hashed against ours."""
    dataset = studio.views[VIEW]["dataset_id"]
    studio.samples[dataset] = [served_row(i, f"train {i}", "humorous") for i in range(250)]
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    link = made.json()
    assert link["check_level"] == "counts_only"
    content = link["checks"]["content"]
    assert content["ran"] is False and "ours_sha256" not in content
    assert "train split and does not name it" in content["reason"]
    assert content["served"]["served_rows"] == 250
    assert len(studio.calls("GET", f"/api/v1/datasets/{dataset}/samples")) == 3


async def test_a_view_miStudio_cannot_serve_links_counts_only_with_the_reason(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    del studio.samples[studio.views[VIEW]["dataset_id"]]  # 404, as miStudio answers
    version_id = labelled_version(data_dir, gate_texts())
    made = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert made.status_code == 201, made.text
    content = made.json()["checks"]["content"]
    assert made.json()["check_level"] == "counts_only" and content["ran"] is False
    assert "refused to serve the rows" in content["reason"] and "not found" in content["reason"]


async def test_a_samples_page_that_is_not_json_refuses_instead_of_weakening_the_link(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    """Seen on production 2026-10-07: a backend route briefly answered with the frontend's HTML.
    That must never quietly make a permanent counts-only link."""
    studio.fail("GET /datasets/{id}/samples", non_json=True)
    version_id = labelled_version(data_dir, gate_texts())
    refused = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert refused.status_code == 502 and refused.json()["error"]["code"] == "mistudio_not_json"
    assert links() == []


async def test_an_evaluation_miStudio_did_not_record_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    refused = await client.post(
        f"{API}/reproduction-links",
        json=link_body(version_id, probe_dataset_id="pmd_eaa518e859d3"),  # the training view
    )
    assert refused.status_code == 404, refused.text
    error = refused.json()["error"]
    assert error["code"] == "evaluation_not_found"
    assert VIEW in error["message"] and OOD_VIEW in error["message"]


async def test_an_unused_link_can_be_deleted(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    link = (await client.post(f"{API}/reproduction-links", json=link_body(version_id))).json()
    again = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert again.status_code == 409 and again.json()["error"]["details"]["link_id"] == link["id"]
    gone = await client.delete(f"{API}/reproduction-links/{link['id']}")
    assert gone.status_code == 204
    assert links() == []


# --- standalone: a missing sibling is named, never a 500 -----------------------------------------


async def test_linking_without_miStudio_configured_names_the_setting(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_db: None,
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    version_id = labelled_version(data_dir, gate_texts())
    refused = await client.post(f"{API}/reproduction-links", json=link_body(version_id))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "mistudio_not_configured" and "MISTUDIO_BASE_URL" in error["message"]
    listed = await client.get(f"{API}/reproduction-links")
    assert listed.status_code == 200 and listed.json() == {"items": []}


async def test_the_refusal_says_miStudio_is_not_configured_when_it_is_not(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    refused = await plan(client, body(make_version(data_dir, rows(3))))
    assert refused.status_code == 422, refused.text
    found = refused.json()["error"]["details"]["link_candidates"]
    assert found["items"] == [] and "MISTUDIO_BASE_URL" in found["reason"]


async def test_the_plan_without_miLLM_configured_names_the_setting(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    labeling: Labeling,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "millm_base_url", None)
    refused = await plan(client, body(make_version(data_dir, rows(3))))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "PROBE_ENDPOINT_UNCONFIGURED" and "MILLM_BASE_URL" in error["message"]
    listed = await client.get(f"{API}/labeling/probes")
    assert listed.status_code == 409
    assert "MILLM_BASE_URL" in listed.json()["error"]["message"]


# --- an agent's link waits for the operator ---------------------------------------------------------


async def test_an_agent_link_waits_for_approval_and_records_it(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, studio: FakeMiStudio
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    pending = await client.post(
        f"{API}/reproduction-links", json=link_body(version_id), headers=AGENT
    )
    assert pending.status_code == 202, pending.text
    assert pending.json()["action"] == "gate_target_write"
    assert links() == [] and studio.count("GET", "/api/v1/probe-monitors/probes/") == 0
    approved = await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    [link] = links()
    assert link.created_by == "agent:dataworks-mcp" and link.created_by_origin == "agent"
    assert link.approval_id == pending.json()["approval_id"] and link.approved_by == operator_name


# --- target priority ----------------------------------------------------------------------------


def _link_row(
    version_id: str,
    role: str,
    created_offset: int,
    auroc: float,
    probe: str = SOURCE,
    *,
    check_level: str = "counts_only",
) -> str:
    with sync_session_factory()() as db:
        row = ReproductionLink(
            id=new_id("rpl"),
            mistudio_base_url=BASE,
            mistudio_probe_id=probe,
            probe_dataset_id=f"pmd_{role}_{created_offset}",
            role=role,
            version_id=version_id,
            split="test",
            input_column="text",
            label_column="label",
            label_mapping={"humorous": "positive", "not_humorous": "negative"},
            auroc=auroc,
            ci_low=auroc - 0.01,
            ci_high=auroc + 0.01,
            n_positive=30,
            n_negative=30,
            check_level=check_level,
            checks={},
            scoring_form={},
            evidence={},
            created_by="Test Operator",
            created_by_origin="operator",
        )
        db.add(row)
        db.commit()
        db.execute(
            text("ALTER TABLE dw_reproduction_links DISABLE TRIGGER dw_reproduction_links_frozen")
        )
        db.execute(
            text(
                "UPDATE dw_reproduction_links SET created_at = now() + make_interval(secs => :o) "
                "WHERE id = :i"
            ),
            {"o": created_offset, "i": row.id},
        )
        db.execute(
            text("ALTER TABLE dw_reproduction_links ENABLE TRIGGER dw_reproduction_links_frozen")
        )
        db.commit()
        return row.id


def test_within_links_in_distribution_wins_then_the_newer(data_dir: Path, clean_db: None) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    _link_row(version_id, "ood_eval", 30, 0.70)  # newest, but out of distribution
    older_id = _link_row(version_id, "id_test", 0, 0.90)
    newer_id = _link_row(version_id, "id_test", 10, 0.95)
    with sync_session_factory()() as db:
        target = reproduction.target_for(db, SOURCE)
    assert target.source == "linked_mistudio_evaluation" and target.link_id == newer_id
    assert target.link_id != older_id and target.role == "id_test"
    assert target.auroc == pytest.approx(0.95) and target.ci == pytest.approx((0.94, 0.96))


async def test_a_snapshot_target_keeps_priority_over_a_link(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,  # noqa: F811
    sender: Any,
) -> None:
    await refreshed(client, sender)
    version_id = labelled_version(data_dir, gate_texts())
    link_id = _link_row(version_id, "id_test", 10, 0.50, probe=MISTUDIO_PROBE)
    planned = await plan(client, body(make_version(data_dir, rows(3))))
    assert planned.status_code == 200, planned.text
    gate = planned.json()["reproduction"]
    assert gate["source"] == "detector_results" and gate["link_id"] is None
    assert gate["snapshot_id"] and gate["mistudio_auroc"] == pytest.approx(0.9750930056392559)
    assert link_id


# --- a linked version and 002's delete ----------------------------------------------------------


async def test_an_unused_link_refuses_its_version_delete_and_a_used_one_is_no_target_after_it(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    imported: Labeling,
    studio: FakeMiStudio,
) -> None:
    version_id = labelled_version(data_dir, gate_texts())
    link = (await client.post(f"{API}/reproduction-links", json=link_body(version_id))).json()
    refused = await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "superseded"}
    )
    assert refused.status_code == 409, refused.text
    assert link["id"] in refused.json()["error"]["message"]

    # Once a run has used the link it is evidence: the version may go, the link stays, and the
    # gate no longer offers it as a target (its rows are gone).
    started = await client.post(f"{API}/label-runs", json=body(make_version(data_dir, rows(3))))
    assert imported.run_until_done(started.json()["id"]).state == "completed"
    deleted = await client.request(
        "DELETE", f"/api/v1/versions/{version_id}", json={"reason": "superseded"}
    )
    assert deleted.status_code == 200, deleted.text
    assert [r.id for r in links()] == [link["id"]]
    with sync_session_factory()() as db, pytest.raises(reproduction.ReproductionUnavailable):
        reproduction.target_for(db, SOURCE)
