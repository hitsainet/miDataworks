"""A run against the label column its data already has (novice re-walk 2026-10-19, finding 6).

The walk's probe labeled 11.9 % of ColBERT positive while ColBERT's own ``humor`` column is about
half positive, and nothing said so. ``GET /label-runs/{id}/agreement`` compares them. Expected
numbers here are computed independently from the stored labels and the column the test wrote, so
the fixture cannot agree with the code by construction: the column is NOT the run's outcome.
"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest
from sklearn.metrics import roc_auc_score
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.label import Label
from src.models.label_run import LabelRun
from src.services import label_agreement
from tests.integration.labeling.helpers import setup_classifier, start_body, texts
from tests.support.labeling_fixtures import Labeling, make_version, row_key

N = 45
HUMOR = [i % 3 == 0 for i in range(N)]  # a third positive: not the run's outcome
RATING = [1 if i % 2 else 0 for i in range(N)]
KIND = ["joke" if i % 4 else "fact" for i in range(N)]  # two strings: which one is positive?
SOURCE = [f"site {i % 5}" for i in range(N)]  # five values: never a candidate


async def labeled(client: httpx.AsyncClient, lab: Labeling, **extra: Any) -> tuple[str, str]:
    columns = extra or {"humor": HUMOR, "rating": RATING, "kind": KIND, "source": SOURCE}
    version_id, template_id = await setup_classifier(client, lab, n=N, extra=columns)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    assert lab.run_until_done(run["id"]).state == "completed"
    return run["id"], version_id


def stored(run_id: str) -> list[tuple[int, str, float | None]]:
    """(row index, outcome, probability) per label, the row found by its text."""
    by_text = {t: i for i, t in enumerate(texts(N))}
    by_key = {row_key(t): i for t, i in by_text.items()}
    with sync_session_factory()() as db:
        rows = db.execute(
            select(Label.row_key, Label.outcome, Label.probability).where(
                Label.label_run_id == run_id
            )
        ).all()
    return [(by_key[k], o, p) for k, o, p in rows]


async def test_the_run_is_compared_with_the_boolean_column(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run_id, _ = await labeled(client, labeling)
    answer = (await client.get(f"/api/v1/label-runs/{run_id}/agreement")).json()
    assert answer["applicable"] is True, answer
    # Every two-valued column is listed, the boolean first; the text the labeler read, the
    # bookkeeping columns and a five-valued column are not.
    assert [c["name"] for c in answer["columns"]] == ["humor", "kind", "rating"]
    assert answer["column"] == "humor" and answer["positive_value"] == "true"

    rows = [(i, o, p) for i, o, p in stored(run_id) if o in ("positive", "negative")]
    predicted = np.array([o == "positive" for _, o, _ in rows])
    actual = np.array([HUMOR[i] for i, _, _ in rows])
    assert 0 < predicted.sum() < len(rows), "the fixture must label both ways"
    assert not np.array_equal(predicted, actual), "the column must not be the run's outcome"
    assert answer["counts"] == {
        "both_positive": int(np.sum(predicted & actual)),
        "run_only_positive": int(np.sum(predicted & ~actual)),
        "column_only_positive": int(np.sum(~predicted & actual)),
        "both_negative": int(np.sum(~predicted & ~actual)),
    }
    assert answer["rows_compared"] == len(rows)
    assert answer["rows_not_compared"] == len(stored(run_id)) - len(rows)
    assert answer["accuracy"] == pytest.approx(float(np.mean(predicted == actual)))
    assert answer["run_positive_share"] == pytest.approx(float(predicted.mean()))
    assert answer["column_positive_share"] == pytest.approx(float(actual.mean()))
    expected_auroc = roc_auc_score(actual.astype(int), [p for _, _, p in rows])
    assert answer["auroc"] == pytest.approx(expected_auroc)
    assert answer["auroc_from"] == "probability"
    assert f"{answer['run_positive_share']:.1%}" in answer["note"]
    assert "'humor'" in answer["note"] and "has not checked it" in answer["note"]


async def test_a_zero_one_column_and_a_named_column(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run_id, _ = await labeled(client, labeling)
    rating = (await client.get(f"/api/v1/label-runs/{run_id}/agreement?column=rating")).json()
    assert rating["applicable"] is True and rating["positive_value"] == "1"
    rows = [(i, o) for i, o, _ in stored(run_id) if o in ("positive", "negative")]
    both = sum(1 for i, o in rows if o == "positive" and RATING[i] == 1)
    assert rating["counts"]["both_positive"] == both


async def test_two_words_ask_which_one_is_positive(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run_id, _ = await labeled(client, labeling)
    asked = (await client.get(f"/api/v1/label-runs/{run_id}/agreement?column=kind")).json()
    assert asked["applicable"] is False and asked["needs_positive_value"] is True
    assert asked["values"] == ["fact", "joke"] and asked["counts"] is None
    told = await client.get(
        f"/api/v1/label-runs/{run_id}/agreement?column=kind&positive_value=joke"
    )
    assert told.json()["applicable"] is True and told.json()["positive_value"] == "joke"
    rows = [(i, o) for i, o, _ in stored(run_id) if o in ("positive", "negative")]
    assert told.json()["counts"]["both_positive"] == sum(
        1 for i, o in rows if o == "positive" and KIND[i] == "joke"
    )
    wrong = await client.get(f"/api/v1/label-runs/{run_id}/agreement?column=kind&positive_value=x")
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "agreement_value_invalid"


async def test_columns_that_cannot_be_compared_are_refused_or_explained(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run_id, _ = await labeled(client, labeling)
    many = (await client.get(f"/api/v1/label-runs/{run_id}/agreement?column=source")).json()
    assert many["applicable"] is False and "more than two" in many["reason"]
    for column in ("text", "_dw_origin"):
        refused = await client.get(f"/api/v1/label-runs/{run_id}/agreement?column={column}")
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == "agreement_column_invalid"
    unknown = await client.get(f"/api/v1/label-runs/{run_id}/agreement?column=nope")
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_column"


async def test_a_version_without_a_label_column_says_so(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    run_id, _ = await labeled(client, labeling, source=SOURCE)
    answer = (await client.get(f"/api/v1/label-runs/{run_id}/agreement")).json()
    assert answer["applicable"] is False and answer["columns"] == []
    assert "no column with exactly two values" in answer["reason"]


async def test_the_card_names_the_version_by_dataset_and_number(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """Re-walk finding 9: the cards read "version 72b1ec75"."""
    run_id, version_id = await labeled(client, labeling)
    run = (await client.get(f"/api/v1/label-runs/{run_id}")).json()
    from src.models.dataset import Dataset
    from src.models.version import Version

    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        dataset = db.get(Dataset, version.dataset_id)
        assert dataset is not None
        expected = (dataset.name, version.number)
    assert (run["input_dataset_name"], run["input_version_number"]) == expected
    listed = (await client.get("/api/v1/label-runs")).json()["items"]
    assert [(r["input_dataset_name"], r["input_version_number"]) for r in listed] == [expected]


def test_the_column_the_labeler_read_is_never_a_candidate(labeling: Labeling) -> None:
    """A two-valued column the labeler read is the input, not a label to compare with."""
    version_id = make_version(
        labeling.data_dir, texts(6), extra={"flag": [True, False] * 3, "other": ["a", "b"] * 3}
    )
    path = labeling.data_dir / f"versions/{version_id}/train.parquet"
    found = [c.name for c in label_agreement._candidates([path], set())]
    assert found == ["flag", "other"]
    assert [c.name for c in label_agreement._candidates([path], {"flag"})] == ["other"]


def test_which_value_is_positive() -> None:
    run = LabelRun(positive_label="funny", negative_label="not funny")
    assert label_agreement.positive_of(["false", "true"], run) == "true"
    assert label_agreement.positive_of(["0", "1"], run) == "1"
    assert label_agreement.positive_of(["No", "Yes"], run) == "Yes"
    assert label_agreement.positive_of(["funny", "not funny"], run) == "funny"
    assert label_agreement.positive_of(["fact", "joke"], run) is None


def test_the_score_is_the_probability_or_the_probe_score() -> None:
    assert label_agreement._score(0.25, {"score": 9.0}) == 0.25
    assert label_agreement._score(None, {"score": 101.5}) == 101.5
    assert label_agreement._score(None, {"score": True}) is None
    assert label_agreement._score(None, None) is None


@pytest.mark.parametrize(
    ("accuracy", "words"),
    [(0.95, "close agreement"), (0.8, "partial agreement"), (0.6, "weak agreement")],
)
def test_the_note_says_how_close_in_plain_words(accuracy: float, words: str) -> None:
    note = label_agreement.note_for(
        column="humor", n=25000, accuracy=accuracy, run_share=0.119, column_share=0.5, partial=False
    )
    assert words in note
    assert "11.9%" in note and "50.0%" in note and "25,000" in note
    assert "far fewer rows positive than the column" in note
