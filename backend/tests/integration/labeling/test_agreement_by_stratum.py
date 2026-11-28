"""Agreement by stratum (017 FTASKS 9.10; FR-017.43; FTID §3.8).

A paid classifier run (TypeSafe's fake answers yes when the text holds "joke") over rows whose
``truth`` column the test wrote: the run agrees with it in genres ``ga`` and ``gb`` and disagrees
on half of ``gc``; ``gd``'s rows are generated and the run reads source rows only, so ``gd`` is
in the selection but nothing was scored there. Every per-stratum figure is computed here by hand
(sklearn for AUROC, the textbook kappa), never by the code under test. Control M21 (group by the
wrong column) turns this red.
"""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score, roc_auc_score

from src.services.label_agreement import kappa
from tests.support.labeling_fixtures import make_version
from tests.support.second_opinion import Paid, body, paid, template_id

__all__ = ["paid"]


def fixture() -> tuple[list[str], list[str], list[bool], list[str], list[str]]:
    texts, genres, truth, origins, kinds = [], [], [], [], []
    for genre, n in (("ga", 12), ("gb", 10), ("gc", 16), ("gd", 6)):
        for i in range(n):
            positive = i % 2 == 0
            says_yes = positive if genre != "gc" else (positive if i < 8 else not positive)
            texts.append(f"{genre} row {i:02d}" + (" joke" if says_yes else ""))
            genres.append(genre)
            truth.append(positive)
            origins.append("generated" if genre == "gd" else "source")
            kinds.append("k1" if i % 3 else "k2")
    return texts, genres, truth, origins, kinds


def by_hand(genres: list[str], truth: list[bool], texts: list[str], genre: str) -> dict[str, Any]:
    idx = [i for i, g in enumerate(genres) if g == genre]
    actual = np.array([truth[i] for i in idx])
    predicted = np.array(["joke" in texts[i] for i in idx])
    score = np.where(predicted, 0.8, 0.1)
    return {
        "counts": {
            "both_positive": int(np.sum(predicted & actual)),
            "run_only_positive": int(np.sum(predicted & ~actual)),
            "column_only_positive": int(np.sum(~predicted & actual)),
            "both_negative": int(np.sum(~predicted & ~actual)),
        },
        "agreement": float(np.mean(predicted == actual)),
        "kappa": float(cohen_kappa_score(actual, predicted)),
        "auroc": float(roc_auc_score(actual, score)),
    }


async def started(client: httpx.AsyncClient, paid: Paid) -> str:
    texts, genres, truth, origins, kinds = fixture()
    v = make_version(
        paid.lab.data_dir, texts, origins, extra={"genre": genres, "truth": truth, "kind": kinds}
    )
    response = await client.post(
        "/api/v1/label-runs",
        json=body(
            v, await template_id(client), target_column="truth", row_filter={"origin": "source"}
        ),
    )
    assert response.status_code == 201, response.text
    run_id = str(response.json()["id"])
    assert paid.lab.run_until_done(run_id).state == "completed"
    return run_id


async def test_per_stratum_figures_equal_a_hand_computation_worst_first(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    run_id = await started(client, paid)
    texts, genres, truth, _, _ = fixture()
    answer = (
        await client.get(f"/api/v1/label-runs/{run_id}/agreement", params={"by": ["genre"]})
    ).json()
    assert answer["applicable"] is True and answer["column"] == "truth"
    assert answer["partial"] is False and answer["by"] == ["genre"]
    strata = answer["strata"]
    cells = strata["cells"]
    assert [c["stratum"]["genre"] for c in cells] == ["gc", "ga", "gb", "gd"]  # worst first
    for cell in cells[:3]:
        genre = cell["stratum"]["genre"]
        expected = by_hand(genres, truth, texts, genre)
        assert cell["counts"] == expected["counts"], genre
        assert cell["agreement"] == pytest.approx(expected["agreement"])
        assert cell["kappa"] == pytest.approx(expected["kappa"])
        assert cell["auroc"] == pytest.approx(expected["auroc"])
        assert cell["rows"] == cell["scored"] == genres.count(genre)
    empty = cells[3]
    assert empty["stratum"] == {"genre": "gd"} and empty["scored"] == 0
    assert empty["agreement"] == empty["kappa"] == empty["auroc"] == "not measured"
    assert empty["counts"] is None and strata["cells_not_measured"] == 1
    assert cells[0]["agreement"] == pytest.approx(0.5)


async def test_the_default_strata_and_paging(client: httpx.AsyncClient, paid: Paid) -> None:
    run_id = await started(client, paid)
    first = (await client.get(f"/api/v1/label-runs/{run_id}/agreement", params={"limit": 3})).json()
    assert first["by"] == ["genre", "kind"]  # FR-017.43's defaults the version has
    total = first["strata"]["cells_total"]
    assert total == 8 and len(first["strata"]["cells"]) == 3
    seen = [c["stratum"] for c in first["strata"]["cells"]]
    token = first["strata"]["next_page_token"]
    while token:
        page = (
            await client.get(
                f"/api/v1/label-runs/{run_id}/agreement",
                params={"limit": 3, "page_token": token},
            )
        ).json()
        seen += [c["stratum"] for c in page["strata"]["cells"]]
        token = page["strata"]["next_page_token"]
    assert len(seen) == total and len({tuple(sorted(s.items())) for s in seen}) == total
    bad = await client.get(f"/api/v1/label-runs/{run_id}/agreement", params={"page_token": "nope"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "page_token_invalid"
    unknown = await client.get(
        f"/api/v1/label-runs/{run_id}/agreement", params={"by": ["no_such_column"]}
    )
    assert unknown.status_code == 422


def test_kappa_is_cohens_and_undefined_for_one_class() -> None:
    actual = [1, 1, 0, 0, 1, 0, 1, 1]
    predicted = [1, 0, 0, 0, 1, 1, 1, 1]
    tp = sum(a and p for a, p in zip(actual, predicted, strict=True))
    fp = sum((not a) and p for a, p in zip(actual, predicted, strict=True))
    fn = sum(a and (not p) for a, p in zip(actual, predicted, strict=True))
    tn = sum((not a) and (not p) for a, p in zip(actual, predicted, strict=True))
    assert kappa(tp, fp, fn, tn) == pytest.approx(cohen_kappa_score(actual, predicted))
    assert kappa(5, 0, 0, 0) is None and kappa(0, 0, 0, 0) is None
