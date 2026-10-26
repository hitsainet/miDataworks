"""One text-baseline fitter, grouped folds and a reference at equal n (017 FTASKS 10.2, 10.3;
FR-017.45; OQ-017-3 (a)).

Rows whose wording gives the label away ONLY through genre-specific words: every genre has its own
positive and negative words. Folds that have seen every genre read the words (AUROC near 1);
holding each genre out once, the held-out genre's words were never seen, so the grouped figure
falls toward chance. The grouped figure is reported beside the stratified one (control M24 swaps
the grouped fold for a stratified one and turns this red). With a reference version both figures
share n. A genre holding one class only is warned by the metadata audit; ``cell_balancer`` over
genre balances it, for fixture-shaped rows of BOTH behaviours.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.services.curation import text_baseline
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version, run_operator

ROLES = {"text": "content", "label": "metadata", "genre": "metadata"}
FILLER = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
#: per fixture behaviour: its genres (FR-017.12 values are behaviour data)
GENRES = {
    "fixture-alpha": ("email", "chat", "report"),
    "fixture-beta": ("memo", "forum"),
}


def genre_rows(genres: tuple[str, ...], n: int = 240, seed: int = 5) -> list[dict[str, Any]]:
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    rows = []
    for i in range(n):
        genre = genres[i % len(genres)]
        label = "yes" if (i // len(genres)) % 2 else "no"
        cue = f"{genre}{'pos' if label == 'yes' else 'neg'}{rng.randint(0, 2)}"
        words = [rng.choice(FILLER) for _ in range(8)] + [cue]
        rng.shuffle(words)
        rows.append({"text": " ".join(words) + f" #{i}", "label": label, "genre": genre})
    return rows


async def audit(client: httpx.AsyncClient, rows: list[dict[str, Any]], **body: Any) -> Any:
    with sync_session_factory()() as db:
        v = make_version(db, with_system_columns(rows), ROLES)
    response = await client.post(
        f"/api/v1/versions/{v.id}/shortcut-audit",
        json={"label_column": "label", "text_baseline": True, **body},
        params={"detail": "full"},
    )
    assert response.status_code == 200, response.text
    return str(v.id), response.json()["report"]


async def warned(client: httpx.AsyncClient, rows: list[dict[str, Any]]) -> set[str]:
    """The columns the stored audit warns on (the read route's ``warnings``)."""
    version_id, _ = await audit(client, rows)
    read = (await client.get(f"/api/v1/versions/{version_id}/shortcut-audit")).json()
    return {str(w["column"]) for w in read["warnings"]}


async def test_the_grouped_figure_is_reported_beside_the_stratified_one(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    _, report = await audit(client, genre_rows(GENRES["fixture-alpha"]), group_folds_by="genre")
    baseline = report["result"]["text_baseline"]
    assert report["params"]["group_folds_by"] == "genre"
    assert baseline["tfidf_logreg"] >= 0.9  # every genre seen: the words give it away
    grouped = baseline["grouped"]
    assert grouped["by"] == "genre" and grouped["kind"] == "leave_one_value_out"
    assert grouped["values"] == 3 and grouped["computed"] is True
    assert grouped["tfidf_logreg"] < 0.7, grouped  # a genre never seen: its words are not known


async def test_with_a_reference_both_figures_share_n(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    rng = random.Random(9)  # noqa: S311
    reference_rows = [
        {"text": " ".join(rng.choice(FILLER) for _ in range(9)) + f" r{i}",
         "label": "yes" if i % 2 else "no", "genre": "ref"}
        for i in range(120)
    ]  # fmt: skip
    ref_id, _ = await audit(client, reference_rows)
    _, report = await audit(client, genre_rows(GENRES["fixture-beta"]), reference_version_id=ref_id)
    found = report["result"]["text_baseline"]["reference"]
    assert found["version_id"] == ref_id and found["computed"] is True
    assert found["n"] == 120  # the smaller side's size, both scored at it
    assert found["tfidf_logreg"] > found["reference_tfidf_logreg"] + 0.2


async def test_the_options_need_the_text_baseline(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, with_system_columns(genre_rows(("a", "b"))), ROLES)
    response = await client.post(
        f"/api/v1/versions/{v.id}/shortcut-audit",
        json={"label_column": "label", "group_folds_by": "genre"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "text_baseline_required"


def test_wording_auroc_holds_each_value_out_once(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = genre_rows(GENRES["fixture-alpha"])
    texts = [r["text"] for r in rows]
    y = [1 if r["label"] == "yes" else 0 for r in rows]
    groups = [r["genre"] for r in rows]
    seen: list[set[str]] = []
    real = text_baseline.fit_predict
    group_of = dict(zip(texts, groups, strict=True))

    def spy(train: list[str], train_y: list[int], test: list[str]) -> list[float]:
        seen.append({group_of[t] for t in test})
        assert not {group_of[t] for t in train} & {group_of[t] for t in test}
        return real(train, train_y, test)

    result = text_baseline.wording_auroc(texts, y, groups, kind="leave_one_value_out", predict=spy)
    assert sorted(next(iter(s)) for s in seen) == sorted(GENRES["fixture-alpha"])
    assert result.folds == 3 and result.kind == "leave_one_value_out"
    stratified = text_baseline.wording_auroc(texts, y)
    assert stratified.auroc is not None and result.auroc is not None
    assert stratified.auroc > result.auroc + 0.2


@pytest.mark.parametrize("key", sorted(GENRES))
async def test_a_genre_holding_one_class_warns_and_the_cell_balancer_fixes_it(
    client: httpx.AsyncClient, operator_name: str, key: str
) -> None:
    genres = GENRES[key]
    rows = genre_rows(genres)
    for row in rows:  # the first genre: positives only (its negatives become another genre's)
        if row["genre"] == genres[0] and row["label"] == "no":
            row["genre"] = genres[1]
    assert "genre" in await warned(client, rows)
    balanced_rows = genre_rows(genres)
    ran = run_operator(
        "cell_balancer",
        {"column": "genre", "label_column": "label"},
        with_system_columns(balanced_rows),
        ROLES,
    )
    counts: dict[tuple[str, str], int] = {}
    for r in ran.output.to_pylist():
        counts[(r["genre"], r["label"])] = counts.get((r["genre"], r["label"]), 0) + 1
    assert len(set(counts.values())) == 1 and len(counts) == 2 * len(genres)
    assert "genre" not in await warned(
        client, ran.output.select(["text", "label", "genre"]).to_pylist()
    )
