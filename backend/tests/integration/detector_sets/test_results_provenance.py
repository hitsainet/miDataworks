"""Feature 011 phases B-D in read-back: runs found by filter (XR-4), the paired score from
miStudio's per-evaluation arrays first (XR-2), each role's commit check in the snapshot (FTASKS
3.6, 4.1, 4.2, 5.1 - 5.5; FR-011.8, FR-011.10 - FR-011.18).

The REAL refresh route runs against the fake miStudio; the scores miStudio serves are joined to the
send's REAL published build file. Served positions are sparse and out of file order, so a join by
response order would pair scores with the wrong rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pyarrow.parquet as pq
import pytest
from sklearn.metrics import roc_auc_score

from src.core.database import sync_session_factory
from src.models import Publish
from src.models.publish import PublishBuild
from src.services.publishing import build_service
from tests.integration.detector_sets.test_results_and_marks import sent_set, serve_run_c
from tests.support.send_fixtures import API, SendDriver

PROBE = "pm_935fc9088482"
TEST_EVAL, OOD_EVAL = "pme_417903d0b231", "pme_99ac14834f92"
OTHER = "d" * 40
MAPPING = {"humorous": "positive", "not_humorous": "negative"}


def ood_of(send: dict[str, Any]) -> dict[str, Any]:
    """The OOD role (the one with a pair column): its view, download, commit and published file."""
    role = next(r for r in send["snapshot"]["roles"] if r["role"] == "ood_eval")
    register = next(
        s for s in send["steps"] if s["step"] == "register" and s["unit_key"] == role["id"]
    )
    download = next(
        s for s in send["steps"] if s["step"] == "download" and role["id"] in s["role_ids"]
    )
    publish = next(
        s for s in send["steps"] if s["step"] == "publish" and s["unit_key"] == role["version_id"]
    )
    with sync_session_factory()() as db:
        pub = db.get(Publish, publish["publish_id"])
        assert pub is not None
        build = db.get(PublishBuild, pub.build_id)
        assert build is not None and build.files
        entry = next(f for f in build.files if f["name"] == role["split"])
        path = build_service.build_file_path(build, entry["path"])
    return {
        "role": role,
        "view": register["probe_dataset_id"],
        "dataset": download["mistudio_dataset_id"],
        "commit": download["commit"],
        "path": path,
    }


def scores_for(
    ood: dict[str, Any], *, keep: list[int] | None = None, seed: int = 7
) -> dict[str, Any]:
    """What miStudio 035 would serve for the OOD evaluation: kept positions (sparse, shuffled),
    scores that separate the classes imperfectly, the mapped labels, and the AUROC they give."""
    table = pq.read_table(ood["path"], columns=["label"])
    values = table.column(0).to_pylist()
    rng = np.random.default_rng(seed)
    positions = keep if keep is not None else [p for p in range(len(values)) if p % 7 != 3]
    positions = list(rng.permutation(positions))
    labels = [1 if MAPPING[values[p]] == "positive" else 0 for p in positions]
    scores = [float(0.55 * y + rng.normal(0, 0.35)) for y in labels]
    return {
        "dataset_id": ood["view"],
        "source_dataset_id": ood["dataset"],
        "revision": ood["commit"],
        "reported_auroc": float(roc_auc_score(labels, scores)),
        "rows": [
            {"position": int(p), "score": s, "label": y, "n_scored": 9}
            for p, s, y in zip(positions, scores, labels, strict=True)
        ],
    }


async def refresh(client: httpx.AsyncClient, set_id: str) -> dict[str, Any]:
    response = await client.post(f"{API}/detector-sets/{set_id}/results/refresh")
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def ood_entry(snap: dict[str, Any]) -> dict[str, Any]:
    [figure] = snap["evaluations"]
    entry: dict[str, Any] = next(s for s in figure["sets"] if s["role"] == "ood_eval")
    return entry


@pytest.fixture
def millm_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The miLLM source, made available with scores inside miStudio's reported interval, and
    counted: a source-order test must see whether it was consulted at all."""
    from src.services.detector_sets import reward_marks

    calls: list[str] = []

    def millm_scores_for(session: Any, probe_id: str, role: Any) -> Any:
        calls.append(probe_id)
        labels = [i % 2 == 1 for i in range(120)]
        # AUROC 0.7375 lands inside run C's interval [0.7258, 0.7502]
        rng = np.random.default_rng(3)
        for _ in range(500):
            scores = [float(0.5 * y + rng.normal(0, 0.6)) for y in labels]
            if 0.7258 <= roc_auc_score(labels, scores) <= 0.7502:
                break
        groups = [f"g{i // 2}" for i in range(120)]
        return scores, labels, groups

    monkeypatch.setattr(reward_marks, "millm_scores_for", millm_scores_for)
    return calls


# --- XR-4: runs by filter -----------------------------------------------------------------------


def bury_the_run(sender: SendDriver, unrelated: int = 250) -> None:
    """The set's run is older than ``unrelated`` newer runs of other sets."""
    [own, *_] = sender.mistudio.runs
    noise = [
        {**own, "id": f"pmr_noise{i:04d}", "train_dataset_id": f"pmd_other{i}"}
        for i in range(unrelated)
    ]
    sender.mistudio.runs = noise + [own]


async def test_the_filter_finds_a_run_older_than_200_unrelated_runs(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_run_filter = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    bury_the_run(sender)
    snap = await refresh(client, set_id)
    assert [r["id"] for r in snap["runs"]] == ["pmr_a2bcffdcd695"]
    assert snap["run_discovery"] == {"method": "filter", "limit": 200}
    [call] = sender.mistudio.calls("GET", "/api/v1/probe-monitors/runs")
    assert call.query == {
        "limit": "200",
        "train_dataset_id": send["run_request"]["train_dataset_id"],
    }


async def test_without_the_filter_the_scan_misses_it_and_says_so(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    bury_the_run(sender)
    snap = await refresh(client, set_id)
    assert snap["runs"] == []
    assert snap["run_discovery"] == {
        "method": "scan",
        "limit": 200,
        "note": "scanned the newest 200 runs; older runs may be missing",
    }
    [call] = sender.mistudio.calls("GET", "/api/v1/probe-monitors/runs")
    assert call.query == {"limit": "200"}


async def test_a_mistudio_that_ignores_the_filter_leaks_no_other_sets_runs(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    from src.services.detector_sets import capabilities

    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    # the document names the parameter, but this miStudio does not apply it
    sender.mistudio.serves_run_filter = True
    sender.mistudio.openapi_doc = sender.mistudio.document()
    sender.mistudio.serves_run_filter = False
    capabilities.clear_cache()
    snap = await refresh(client, set_id)
    assert snap["run_discovery"]["method"] == "filter"
    assert [r["id"] for r in snap["runs"]] == ["pmr_a2bcffdcd695"]  # pmr_other is dropped


# --- FR-011.12: the evaluation behind each figure ------------------------------------------------


async def test_each_set_entry_records_its_own_evaluation_id(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    snap = await refresh(client, set_id)
    [figure] = snap["evaluations"]
    by_role = {s["role"]: s["evaluation_id"] for s in figure["sets"]}
    assert by_role == {"id_test": TEST_EVAL, "ood_eval": OOD_EVAL}


# --- XR-2: the paired score from miStudio first --------------------------------------------------


async def test_the_paired_score_comes_from_mistudios_arrays_joined_by_position(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.clients import mistudio_client as mc

    sender.mistudio.serves_scores = True
    monkeypatch.setattr(mc, "SCORES_PAGE", 40)
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    ood = ood_of(send)
    served = scores_for(ood)
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = served
    snap = await refresh(client, set_id)
    paired = ood_entry(snap)["paired"]
    assert paired["available"] is True, paired
    assert paired["source"] == "mistudio" and paired["skipped"] == []
    assert "within 0.0005" in paired["acceptance"]
    assert millm_calls == []  # miStudio was enough; miLLM was not consulted
    # every page of exactly the recorded evaluation was read, and no other evaluation's
    pages = sender.mistudio.calls("GET", "/api/v1/probe-monitors/probes/")
    scored = [c for c in pages if c.path.endswith("/scores")]
    assert {c.path for c in scored} == {
        f"/api/v1/probe-monitors/probes/{PROBE}/evaluations/{OOD_EVAL}/scores"
    }
    assert [c.query["offset"] for c in scored] == ["0", "40", "80"]
    # the pairs counted are cross-label pairs inside the published pair groups at the KEPT rows
    table = pq.read_table(ood["path"], columns=["label", "pair_id"])
    labels = table.column(0).to_pylist()
    groups = table.column(1).to_pylist()
    kept = {r["position"] for r in served["rows"]}
    by_group: dict[str, list[str]] = {}
    for p in kept:
        by_group.setdefault(groups[p], []).append(labels[p])
    expected_pairs = sum(g.count("humorous") * g.count("not_humorous") for g in by_group.values())
    assert paired["pairs"] == expected_pairs > 0
    # and the paired figure is computed from the joined scores
    score_at = {r["position"]: r["score"] for r in served["rows"]}
    wins = []
    for members in {g: [p for p in kept if groups[p] == g] for g in by_group}.values():
        for a in members:
            for b in members:
                if labels[a] == "humorous" and labels[b] == "not_humorous":
                    wins.append(
                        1.0
                        if score_at[a] > score_at[b]
                        else 0.5 if score_at[a] == score_at[b] else 0.0
                    )
    assert paired["paired"] == round(float(np.mean(wins)), 4)


async def test_with_both_sources_available_mistudio_is_used(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
) -> None:
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = scores_for(ood_of(send))
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["source"] == "mistudio"
    assert millm_calls == []


async def test_a_legacy_evaluation_falls_back_to_millm_with_mistudios_words(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
) -> None:
    sender.mistudio.serves_scores = True
    sender.mistudio.legacy_evaluations.add(OOD_EVAL)
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["available"] is True and paired["source"] == "millm", paired
    assert millm_calls == [PROBE]
    [skip] = paired["skipped"]
    assert skip["source"] == "mistudio"
    assert "recorded before miStudio saved row positions" in skip["reason"]
    assert f"POST /api/v1/probe-monitors/probes/{PROBE}/evaluate" in skip["reason"]


async def test_not_served_names_mistudio_and_millm_as_skipped(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["available"] is False and paired["source"] is None
    assert "T-47" in paired["reason"]
    assert [s["source"] for s in paired["skipped"]] == ["mistudio", "millm"]
    assert paired["skipped"][0]["reason"].startswith("not served by miStudio at")
    assert sender.mistudio.count("GET", "/api/v1/probe-monitors/probes/") == 1  # the report only


def _mutate(served: dict[str, Any], case: str, ood: dict[str, Any]) -> str:
    """Break one thing the join relies on; returns the words its refusal must contain."""
    if case == "commit":
        served["revision"] = OTHER
        return f"miStudio scored the split at {OTHER}"
    if case == "legacy_commit":
        served["revision"] = None
        return "at revision not recorded"
    if case == "dataset":
        served["source_dataset_id"] = "someone-elses-dataset"
        return "this send downloaded"
    if case == "view":
        served["dataset_id"] = "pmd_another_view"
        return "scores are for view pmd_another_view"
    if case == "range":
        served["rows"][0]["position"] = 10_000
        return "position 10000"
    if case == "label":
        served["rows"][0]["label"] = 1 - served["rows"][0]["label"]
        return "disagrees with this role's mapping"
    if case == "auroc_null":
        served["reported_auroc"] = None
        return "reports no AUROC"
    if case == "build_file":
        Path(ood["path"]).unlink()
        return "the published build file is gone"
    raise AssertionError(case)


@pytest.mark.parametrize(
    "case",
    ["commit", "legacy_commit", "dataset", "view", "range", "label", "auroc_null", "build_file"],
)
async def test_each_join_refusal_is_named_and_falls_back(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
    case: str,
) -> None:
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    ood = ood_of(send)
    served = scores_for(ood)
    words = _mutate(served, case, ood)
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = served
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["source"] == "millm", paired
    [skip] = paired["skipped"]
    assert skip["source"] == "mistudio" and words in skip["reason"], skip


async def test_an_answer_for_another_evaluation_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
) -> None:
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = scores_for(ood_of(send))
    sender.mistudio.evaluation_id_override[OOD_EVAL] = TEST_EVAL
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["source"] == "millm"
    assert TEST_EVAL in paired["skipped"][0]["reason"]


@pytest.mark.parametrize(("shift", "accepted"), [(0.0, True), (4e-4, True), (2e-3, False)])
async def test_the_5e_4_gate(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    shift: float,
    accepted: bool,
) -> None:
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    served = scores_for(ood_of(send))
    served["reported_auroc"] += shift
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = served
    paired = ood_entry(await refresh(client, set_id))["paired"]
    if accepted:
        assert paired["source"] == "mistudio", paired
    else:
        assert paired["source"] is None
        reason = paired["skipped"][0]["reason"]
        assert reason.startswith("AUROC") and "is not within 0.0005" in reason
        assert paired["reason"] == reason  # the most specific reproduction refusal


# --- FR-011.8: each role's commit in the snapshot -----------------------------------------------


@pytest.mark.parametrize(("pinned", "check"), [(True, "verified"), (False, "claimed")])
async def test_the_snapshot_records_each_roles_commit_check(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    pinned: bool,
    check: str,
) -> None:
    sender.mistudio.serves_revision = pinned
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    snap = await refresh(client, set_id)
    commits = snap["commits"]
    assert sorted(c["role"] for c in commits) == sorted(
        ["train", "id_test", "ood_eval", "calibration_negatives"]
    )
    phrase = {
        "verified": "verified by miStudio",
        "claimed": "claimed (miStudio does not pin downloads)",
    }[check]
    assert {(c["commit_check"], c["commit_check_phrase"]) for c in commits} == {(check, phrase)}
    downloads = {s["unit_key"]: s for s in send["steps"] if s["step"] == "download"}
    for c in commits:
        step = next(s for s in downloads.values() if c["role_id"] in s["role_ids"])
        assert (
            c["commit"] == step["commit"]
            and c["mistudio_dataset_id"] == step["mistudio_dataset_id"]
        )
    # stored, not recomputed: the read gives the same
    got = (await client.get(f"{API}/detector-sets/{set_id}/results")).json()
    assert got["commits"] == commits and got["run_discovery"] == snap["run_discovery"]


async def test_a_snapshot_from_before_011_reads_not_recorded(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Snapshots are append-only (a trigger refuses UPDATE), so an older snapshot is inserted as
    a pre-011 refresh wrote it: without ``provenance``."""
    from src.models.detector_results import DetectorResults

    set_id, send = await sent_set(client, sender)
    with sync_session_factory()() as db:
        db.add(
            DetectorResults(
                id="dres_before011",
                set_id=set_id,
                send_id=send["id"],
                mistudio_base_url="http://mistudio.test",
                read_by="operator",
                read_by_origin="operator",
                runs=[],
                figures=[],
                report_sha256={},
                gone={"runs": [], "probes": []},
            )
        )
        db.commit()
    got = (await client.get(f"{API}/detector-sets/{set_id}/results?snapshot=dres_before011")).json()
    assert got["id"] == "dres_before011"
    assert got["run_discovery"] is None and got["commits"] is None
