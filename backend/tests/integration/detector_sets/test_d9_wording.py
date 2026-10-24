"""D-9 "wording alone" (015 FR-015.15 - FR-015.17; ENH-005 D55; FTDD 015 section 10).

The two text fixtures share their length and vocabulary distributions; only the label's relation
to one word differs (the give-away puts ``zebra`` in every positive row and ``yak`` in every
negative one; the control assigns the two words at random).
"""

from __future__ import annotations

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.database import sync_session_factory
from src.models.text_separability import TextSeparability
from src.models.version import Version
from src.services.detector_sets import checks, separation_wording, set_service
from src.services.detector_sets.checks import WordingComparison, WordingFacts
from tests.support.labeling_fixtures import make_version

VOCAB = [f"w{i}" for i in range(300)]
MAPPING = {"1": "positive", "0": "negative"}


def texts_and_labels(n: int, *, give_away: bool, seed: int = 7) -> tuple[list[str], list[int]]:
    rng = random.Random(seed)
    texts, labels = [], []
    for i in range(n):
        y = i % 2
        words = [rng.choice(VOCAB) for _ in range(rng.randint(12, 20))]
        marked = y == 1 if give_away else rng.random() < 0.5
        words[rng.randrange(len(words))] = "zebra" if marked else "yak"
        texts.append(" ".join(words) + f" r{i}")
        labels.append(y)
    return texts, labels


# --- the pure figure --------------------------------------------------------------------------


def test_a_one_word_give_away_scores_high_and_the_control_does_not() -> None:
    t, y = texts_and_labels(200, give_away=True)
    high = separation_wording.wording_auroc(t, y)
    t2, y2 = texts_and_labels(200, give_away=False)
    low = separation_wording.wording_auroc(t2, y2)
    assert high.auroc is not None and high.auroc >= 0.97
    assert low.auroc is not None and low.auroc < 0.7
    assert (high.n, high.n_positive, high.n_negative) == (200, 100, 100)


def test_fewer_than_twenty_of_a_class_is_not_computed() -> None:
    t, y = texts_and_labels(38, give_away=True)
    r = separation_wording.wording_auroc(t, y)
    assert r.auroc is None and r.reason == "too_few_rows"


def test_a_pair_never_straddles_a_fold(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a pair column the folds are grouped: no pair id is in a fold's train and test."""
    t, y = texts_and_labels(200, give_away=False)
    groups = [f"p{i // 2}" for i in range(200)]
    group_of = dict(zip(t, groups, strict=True))
    seen: list[tuple[set[str], set[str]]] = []
    real = separation_wording._fit_predict

    def spy(train: list[str], train_y: list[int], test: list[str]) -> list[float]:
        seen.append(({group_of[x] for x in train}, {group_of[x] for x in test}))
        return real(train, train_y, test)

    monkeypatch.setattr(separation_wording, "_fit_predict", spy)
    result = separation_wording.wording_auroc(t, y, groups)
    assert result.grouped and len(seen) == separation_wording.FOLDS
    for train_groups, test_groups in seen:
        assert not train_groups & test_groups


# --- the rule ---------------------------------------------------------------------------------


def facts(auroc: float | None, *comparisons: WordingComparison, reason: str | None = None) -> Any:
    return WordingFacts("train", "set - training", 400, auroc, False, reason, comparisons)


@pytest.mark.parametrize(
    ("f", "outcome", "warned"),
    [
        (facts(0.98), "note", True),
        (facts(0.97), "note", True),
        (facts(0.96), "green", False),
        (facts(0.99, WordingComparison("ood", 400, 0.99, 0.975)), "green", False),
        (facts(0.99, WordingComparison("ood", 400, 0.99, 0.96)), "note", True),
        # most lenient: one reference as separable as the role means no warning
        (
            facts(
                0.99,
                WordingComparison("a", 400, 0.99, 0.90),
                WordingComparison("b", 300, 0.985, 0.98),
            ),
            "green",
            False,
        ),
        (facts(None, reason="too_few_rows"), "note", False),
    ],
)
def test_d9_never_refuses(f: WordingFacts, outcome: str, warned: bool) -> None:
    from tests.unit.detector_sets.test_pure_rules import inputs

    out = checks.evaluate(inputs(wording=(f,)))
    [d9] = [o for o in out if o.code == "D-9"]
    assert d9.outcome == outcome
    assert (d9.details["warned"] == [f.name]) is warned
    assert checks.send_allowed(out)
    if f.auroc is not None:
        assert f"{f.auroc:.3f}" in (d9.figure or "")
    else:
        assert "not computed" in (d9.figure or "") and "not computed" in d9.reason


def test_no_measured_role_is_a_note_with_its_reason() -> None:
    from tests.unit.detector_sets.test_pure_rules import inputs

    [d9] = [o for o in checks.evaluate(inputs(wording=())) if o.code == "D-9"]
    assert d9.outcome == "note" and "Not computed" in d9.reason


# --- the cache --------------------------------------------------------------------------------


def _role(**over: Any) -> Any:
    base = {
        "split": "train",
        "input_column": "text",
        "label_column": "label",
        "label_mapping": MAPPING,
        "pair_column": None,
    }
    return SimpleNamespace(**{**base, **over})


def test_the_second_read_does_not_refit(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    t, y = texts_and_labels(120, give_away=True)
    version_id = make_version(data_dir, t, extra={"label": [str(v) for v in y]})
    calls: list[int] = []
    real = separation_wording.wording_auroc

    def spy(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(separation_wording, "wording_auroc", spy)
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        first = set_service.wording_for(db, version, _role(), 2000)
        second = set_service.wording_for(db, version, _role(), 2000)
        assert len(calls) == 1
        assert first.auroc == second.auroc and first.auroc is not None and first.auroc >= 0.9
        assert first.n == 120 and first.n_positive == 60
        # another sample size is another key (D55 compares at equal size)
        set_service.wording_for(db, version, _role(), 80)
        assert len(calls) == 2
        rows = db.query(TextSeparability).filter(TextSeparability.version_id == version_id).all()
        assert sorted(r.sample_n for r in rows) == [80, 2000]
        assert {r.n for r in rows} == {80, 120}


def test_the_sample_is_seeded_and_label_stratified(data_dir: Path) -> None:
    t, y = texts_and_labels(300, give_away=False)
    labels = [str(v) for v in y]
    labels[:60] = ["0"] * 60  # 120 positive / 180 negative
    version_id = make_version(data_dir, t, extra={"label": labels})
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        a = set_service._sample_rows(version, _role(), 100, 0)
        b = set_service._sample_rows(version, _role(), 100, 0)
    assert a == b and len(a[1]) == 100
    assert sum(a[1]) == 40  # the class share is kept: 120/300 of 100


def test_unmapped_and_excluded_rows_are_left_out(data_dir: Path) -> None:
    t, y = texts_and_labels(90, give_away=False)
    labels = [str(v) for v in y]
    labels[:10] = ["2"] * 10
    version_id = make_version(data_dir, t, extra={"label": labels})
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        texts, ys, groups = set_service._sample_rows(
            version, _role(label_mapping={**MAPPING, "2": "excluded"}), 2000, 0
        )
    assert len(ys) == 80 and groups is None
