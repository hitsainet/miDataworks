"""Feature 011 phase A: the fourth capability, the pinned 035 names, and the client's new reads
(FTASKS 2.1 - 2.4; FR-011.1, FR-011.2, FR-011.14; FTDD 011 section 5)."""

from __future__ import annotations

import json

import pytest

from src.clients import mistudio_client as mc
from src.services.detector_sets import capabilities
from tests.support import fake_mistudio
from tests.support.fake_mistudio import BASE, FakeMiStudio

COMMIT = "c" * 40
OTHER = "d" * 40


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMiStudio:
    f = FakeMiStudio()
    monkeypatch.setattr(mc, "TRANSPORT", f.transport())
    capabilities.clear_cache()
    return f


def read(fake: FakeMiStudio) -> capabilities.Capabilities:
    with mc.MiStudioClient(BASE) as client:
        return capabilities.read(client, fresh=True)


# --- FR-011.1: four capabilities ---------------------------------------------------------------


def test_the_recorded_schema_serves_none_of_the_035_names(fake: FakeMiStudio) -> None:
    caps = read(fake)
    assert (
        caps.download_revision,
        caps.per_row_scores,
        caps.runs_train_filter,
        caps.behaviour_meaning,
    ) == (False, False, False, False)
    reasons = caps.as_dict()["reasons"]
    assert set(reasons) == {
        "dataset_version_manifest",
        "download_revision",
        "per_row_scores",
        "runs_train_filter",
        "behaviour_meaning",
    }
    assert reasons["runs_train_filter"] == (
        f"not served by miStudio at {BASE} (read /api/openapi.json)"
    )


@pytest.mark.parametrize(
    ("toggle", "name"),
    [
        ("serves_revision", "download_revision"),
        ("serves_run_filter", "runs_train_filter"),
        ("serves_scores", "per_row_scores"),
    ],
)
def test_each_toggle_flips_exactly_its_capability(
    fake: FakeMiStudio, toggle: str, name: str
) -> None:
    setattr(fake, toggle, True)
    caps = read(fake)
    served = {
        n for n in ("download_revision", "per_row_scores", "runs_train_filter") if getattr(caps, n)
    }
    assert served == {name}
    assert caps.as_dict()["reasons"][name] == "served"


def test_a_path_parameter_or_another_query_name_is_not_the_run_filter(fake: FakeMiStudio) -> None:
    doc = json.loads(json.dumps(fake.openapi_doc))
    doc["paths"]["/api/v1/probe-monitors/runs"]["get"]["parameters"] = [
        {"name": "train_dataset_id", "in": "path"},
        {"name": "train_dataset", "in": "query"},
    ]
    assert capabilities.from_openapi(BASE, doc).runs_train_filter is False
    doc["paths"]["/api/v1/probe-monitors/runs"]["get"]["parameters"].append(
        {"name": "train_dataset_id", "in": "query"}
    )
    assert capabilities.from_openapi(BASE, doc).runs_train_filter is True


# --- FR-011.2: the names miStudio owns, pinned on both sides of the fixture -------------------

#: BRD-MIS-DATAWORKS-002 section 6, exactly. A rename in miStudio 035 must change these strings,
#: the fake's document and miStudio's own ``app.openapi()`` pin together.
PINNED = {
    "download_request_property": ("DatasetDownloadRequest", "revision"),
    "dataset_response_property": ("DatasetResponse", "revision"),
    "scores_path": "/api/v1/probe-monitors/probes/{probe_id}/evaluations/{evaluation_id}/scores",
    "run_filter_param": "train_dataset_id",
}


def test_the_035_names_are_pinned_in_the_fake_and_in_the_capability_read(
    fake: FakeMiStudio,
) -> None:
    fake.serves_revision = fake.serves_run_filter = fake.serves_scores = True
    doc = fake.document()
    schemas = doc["components"]["schemas"]
    for schema, prop in (
        PINNED["download_request_property"],
        PINNED["dataset_response_property"],
    ):
        assert prop in schemas[schema]["properties"], (schema, prop)
    assert PINNED["scores_path"] in doc["paths"]
    assert "get" in doc["paths"][PINNED["scores_path"]]
    runs_params = doc["paths"]["/api/v1/probe-monitors/runs"]["get"]["parameters"]
    assert {"name": PINNED["run_filter_param"], "in": "query", "required": False} in runs_params
    # the capability read uses the same strings
    assert (capabilities.DOWNLOAD_SCHEMA, capabilities.REVISION_PROPERTY) == PINNED[
        "download_request_property"
    ]
    assert capabilities.RUN_FILTER_PARAM == PINNED["run_filter_param"]
    assert PINNED["scores_path"].startswith(capabilities.SCORES_PREFIX)
    assert PINNED["scores_path"].endswith(capabilities.SCORES_SUFFIX)
    assert fake_mistudio.SCORES_PATH == PINNED["scores_path"]
    caps = capabilities.from_openapi(BASE, doc)
    assert caps.download_revision and caps.per_row_scores and caps.runs_train_filter


# --- FR-011.4 seam: one body builder ----------------------------------------------------------


def test_the_download_body_carries_revision_only_when_given() -> None:
    assert mc.download_body("o/r", None, "train") == {
        "repo_id": "o/r",
        "config": None,
        "split": "train",
    }
    assert mc.download_body("o/r", None, "train", COMMIT)["revision"] == COMMIT


def test_an_unserved_revision_key_is_refused_by_the_fake_as_by_miStudio(fake: FakeMiStudio) -> None:
    with mc.MiStudioClient(BASE) as client, pytest.raises(mc.MiStudioRefused) as raised:
        client.download("o/r", None, "train", revision=COMMIT)
    assert raised.value.status == 422


# --- FR-011.5: the revision-aware lookup ------------------------------------------------------


def test_find_dataset_at_matches_only_the_commit_and_names_the_others(fake: FakeMiStudio) -> None:
    fake.serves_revision = True
    legacy = fake.seed_existing_dataset("o/r", "train")
    other = fake.seed_existing_dataset("o/r", "train", revision=OTHER)
    fake.seed_existing_dataset("o/r", "test", revision=COMMIT)  # another split: never a match
    with mc.MiStudioClient(BASE) as client:
        found = client.find_dataset_at("o/r", None, "train", COMMIT)
        assert found.match is None
        assert sorted(found.others) == sorted([OTHER, mc.NOT_RECORDED])
        same = fake.seed_existing_dataset("o/r", "train", revision=COMMIT)
        found = client.find_dataset_at("o/r", None, "train", COMMIT)
    assert found.match is not None and found.match["id"] == same
    assert {legacy, other} - {found.match["id"]} == {legacy, other}


# --- FR-011.10: the filtered run read ---------------------------------------------------------


def test_list_runs_sends_the_filter_only_when_asked(fake: FakeMiStudio) -> None:
    with mc.MiStudioClient(BASE) as client:
        client.list_runs(200)
        client.list_runs(200, train_dataset_id="pmd_x")
    first, second = fake.calls("GET", "/api/v1/probe-monitors/runs")
    assert first.query == {"limit": "200"}
    assert second.query == {"limit": "200", "train_dataset_id": "pmd_x"}


# --- FR-011.14, FR-011.18: the paged scores read ----------------------------------------------


def served_scores(n: int) -> dict[str, object]:
    return {
        "dataset_id": "pmd_eval",
        "source_dataset_id": "ds-1",
        "revision": COMMIT,
        "reported_auroc": 0.75,
        "rows": [
            {"position": i * 2, "score": i / n, "label": i % 2, "n_scored": 7} for i in range(n)
        ],
    }


def test_every_page_is_read_and_concatenated(
    fake: FakeMiStudio, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.serves_scores = True
    fake.scores[("pm_1", "pme_1")] = served_scores(25)
    monkeypatch.setattr(mc, "SCORES_PAGE", 10)
    with mc.MiStudioClient(BASE) as client:
        got = client.iter_evaluation_scores("pm_1", "pme_1")
    assert got["total"] == 25 and len(got["rows"]) == 25
    assert [r["position"] for r in got["rows"]] == [i * 2 for i in range(25)]
    pages = fake.calls("GET", "/api/v1/probe-monitors/probes/pm_1/evaluations/pme_1/scores")
    assert [c.query for c in pages] == [
        {"offset": "0", "limit": "10"},
        {"offset": "10", "limit": "10"},
        {"offset": "20", "limit": "10"},
    ]


def test_a_page_of_another_evaluation_is_refused(fake: FakeMiStudio) -> None:
    fake.serves_scores = True
    fake.scores[("pm_1", "pme_1")] = served_scores(3)
    fake.evaluation_id_override["pme_1"] = "pme_2"
    with mc.MiStudioClient(BASE) as client, pytest.raises(mc.MiStudioScoresRefused) as raised:
        client.iter_evaluation_scores("pm_1", "pme_1")
    assert "pme_2" in raised.value.message


def test_a_legacy_evaluation_raises_positions_not_recorded_with_the_evaluate_call(
    fake: FakeMiStudio,
) -> None:
    fake.serves_scores = True
    fake.legacy_evaluations.add("pme_old")
    with mc.MiStudioClient(BASE) as client, pytest.raises(mc.MiStudioPositionsNotRecorded) as e:
        client.iter_evaluation_scores("pm_1", "pme_old")
    assert e.value.detail["code"] == "positions_not_recorded"
    assert "POST /api/v1/probe-monitors/probes/pm_1/evaluate" in e.value.message


def test_another_scores_refusal_stays_verbatim(fake: FakeMiStudio) -> None:
    fake.serves_scores = True
    with mc.MiStudioClient(BASE) as client, pytest.raises(mc.MiStudioRefused) as raised:
        client.iter_evaluation_scores("pm_1", "pme_missing")
    assert raised.value.status == 404 and "pme_missing" in str(raised.value.detail)
