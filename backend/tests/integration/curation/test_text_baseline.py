"""The shortcut audit's text baseline, through the live route (PII run G7, 2026-10-20).

The audit scored metadata only; the run measured wording and length shortcuts with scikit-learn on
a downloaded copy. ``text_baseline: true`` adds TF-IDF + logistic regression and length-only
AUROC on held-out folds, with a permuted-label control. Two versions are built: one whose WORDING
gives the label away, one whose text carries no signal.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import httpx

from src.core.database import sync_session_factory
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version

ROLES = {"text": "content", "label": "metadata", "topic": "metadata"}
WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi".split()


def _rows(leaky: bool, n: int = 200) -> list[dict[str, Any]]:
    rng = random.Random(7)
    rows = []
    for i in range(n):
        label = "yes" if i % 2 else "no"
        words = [rng.choice(WORDS) for _ in range(rng.randint(5, 12))]
        if leaky:
            words.insert(rng.randint(0, len(words)), "ssn" if label == "yes" else "weather")
        rows.append({"text": " ".join(words) + f" #{i}", "label": label, "topic": "x"})
    return rows


async def _audit(client: httpx.AsyncClient, rows: list[dict[str, Any]], **body: Any) -> Any:
    with sync_session_factory()() as db:
        v = make_version(db, with_system_columns(rows), ROLES)
    response = await client.post(
        f"/api/v1/versions/{v.id}/shortcut-audit", json={"label_column": "label", **body}
    )
    assert response.status_code == 200, response.text
    return v.id, response.json()["report"]


async def test_wording_that_gives_the_label_away_is_reported(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    _, report = await _audit(client, _rows(leaky=True), text_baseline=True)
    baseline = report["result"]["text_baseline"]
    assert report["params"]["text_baseline"] is True
    assert baseline["computed"] is True and baseline["content_columns"] == ["text"]
    assert baseline["tfidf_logreg"] >= 0.95 and baseline["wording_separates_labels"] is True
    assert 0.3 <= baseline["tfidf_permuted_control"] <= 0.7
    assert 0.3 <= baseline["length_only"] <= 0.7
    assert baseline["n_rows"] == 200 and baseline["folds"] == 5
    assert "wording or length" in baseline["interpretation"]


async def test_text_without_signal_is_near_chance(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    _, report = await _audit(client, _rows(leaky=False), text_baseline=True)
    baseline = report["result"]["text_baseline"]
    assert baseline["tfidf_logreg"] < 0.75 and baseline["wording_separates_labels"] is False


async def test_without_the_option_the_audit_and_its_identity_are_unchanged(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str
) -> None:
    _, report = await _audit(client, _rows(leaky=True))
    assert "text_baseline" not in report["result"]
    assert "text_baseline" not in report["params"]


async def test_a_large_input_is_sampled_and_says_so(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str, monkeypatch: Any
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "curation_text_baseline_max_rows", 100)
    _, report = await _audit(client, _rows(leaky=True), text_baseline=True)
    baseline = report["result"]["text_baseline"]
    assert baseline["sampled"] is True and baseline["n_rows"] == 100
    assert "100 of 200" in baseline["sample_note"]
