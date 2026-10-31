"""A behaviour's vocabulary is declared and enforced (017 FR-017.13, FR-017.54; FTASKS 4.1)."""

from __future__ import annotations

import json

import pytest

from src.core.config import get_settings
from src.services.behaviour.vocabulary import (
    VocabularyError,
    largest_remainder,
    parse_shares,
    parse_vocabulary,
)
from tests.support.behaviour_fixtures import BEHAVIOURS, plan_document


def _vocabulary(key: str) -> dict:
    return dict(plan_document(key)["vocabulary"])


@pytest.mark.parametrize("key", BEHAVIOURS)
def test_each_fixture_parses_with_the_same_code(key: str) -> None:
    doc = plan_document(key)
    vocabulary = parse_vocabulary(json.dumps(doc["vocabulary"]))
    shares = parse_shares(json.dumps(doc["shares"]), vocabulary)
    assert list(vocabulary.genres) == doc["vocabulary"]["genres"]
    for dimension, given in doc["shares"].items():
        assert shares[dimension] == given
    for dimension in vocabulary.dimensions():
        assert abs(sum(shares[dimension].values()) - 1.0) < 1e-9


def test_the_two_fixture_vocabularies_are_disjoint() -> None:
    def values(key: str) -> set[str]:
        v = parse_vocabulary(json.dumps(_vocabulary(key)))
        out = {*v.genres, *v.framings, *v.kinds, *v.forms, *(b.name for b in v.length_bands)}
        for sq in v.sub_questions:
            out |= {sq.name, *sq.values}
        return out

    alpha, beta = (values(k) for k in BEHAVIOURS)
    assert alpha and beta and not alpha & beta, alpha & beta


@pytest.mark.parametrize("key", BEHAVIOURS)
def test_an_undeclared_framing_is_refused_naming_the_value_and_field(key: str) -> None:
    vocabulary = parse_vocabulary(json.dumps(_vocabulary(key)))
    declared = vocabulary.framings[0]
    with pytest.raises(VocabularyError) as refused:
        parse_shares(
            json.dumps({"framing": {declared: 0.5, "undeclared-framing": 0.5}}), vocabulary
        )
    assert refused.value.code == "vocabulary_value_undeclared"
    assert refused.value.details["field"] == "shares.framing"
    assert refused.value.details["value"] == "undeclared-framing"
    assert "'undeclared-framing'" in refused.value.message
    assert refused.value.details["next"]["tool"] == "dataworks_validate_recipe"


def test_one_value_over_the_cap_is_refused_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _vocabulary("fixture-alpha")
    # every field at most the longest one: genres is raised to exactly that length
    longest = max(len(v) for v in raw.values() if isinstance(v, list))
    raw["genres"] = [*raw["genres"], *(f"extra-{i}" for i in range(longest - len(raw["genres"])))]
    monkeypatch.setattr(get_settings(), "behaviour_max_vocabulary_values", len(raw["genres"]))
    assert parse_vocabulary(json.dumps(raw)).genres  # at the cap: accepted
    raw["genres"] = [*raw["genres"], "one-more"]
    with pytest.raises(VocabularyError) as refused:
        parse_vocabulary(json.dumps(raw))
    assert refused.value.code == "cap_exceeded"
    assert refused.value.details["setting"] == "BEHAVIOUR_MAX_VOCABULARY_VALUES"
    assert refused.value.details["field"] == "vocabulary.genres"
    assert refused.value.details["next"]["tool"] == "dataworks_get_settings"
    # changing the setting changes the result
    monkeypatch.setattr(get_settings(), "behaviour_max_vocabulary_values", len(raw["genres"]))
    assert len(parse_vocabulary(json.dumps(raw)).genres) == len(raw["genres"])


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"placements": ["sideways"]}, "vocabulary_value_undeclared"),
        ({"genres": []}, "vocabulary_invalid"),
        ({"genres": ["a", "a"]}, "vocabulary_invalid"),
        ({"length_bands": [{"name": "x", "min_chars": 5, "max_chars": 5}]}, "vocabulary_invalid"),
        ({"sub_questions": [{"name": "Bad-Name", "values": ["a", "b"]}]}, "vocabulary_invalid"),
        ({"sub_questions": [{"name": "one", "values": ["only"]}]}, "vocabulary_invalid"),
        ({"surprise": ["x"]}, "vocabulary_invalid"),
    ],
)
def test_malformed_vocabularies_are_refused(change: dict, code: str) -> None:
    raw = {**_vocabulary("fixture-alpha"), **change}
    with pytest.raises(VocabularyError) as refused:
        parse_vocabulary(json.dumps(raw))
    assert refused.value.code == code


def test_shares_that_do_not_add_up_are_refused() -> None:
    vocabulary = parse_vocabulary(json.dumps(_vocabulary("fixture-alpha")))
    with pytest.raises(VocabularyError) as refused:
        parse_shares(json.dumps({"genre": {"email": 0.5, "chat": 0.4}}), vocabulary)
    assert refused.value.code == "vocabulary_invalid" and "adds up to" in refused.value.message
    with pytest.raises(VocabularyError) as dimension:
        parse_shares(json.dumps({"sq:nope": {"x": 1}}), vocabulary)
    assert dimension.value.code == "vocabulary_value_undeclared"


def test_largest_remainder_is_exact_and_deterministic() -> None:
    counts = largest_remainder({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}, 10)
    assert counts == {"a": 4, "b": 3, "c": 3} and sum(counts.values()) == 10
    assert largest_remainder({"x": 0.105, "y": 0.895}, 30_000) == {"x": 3150, "y": 26850}


def test_a_vocabulary_making_more_cells_than_a_plan_has_rows_is_refused() -> None:
    """Review R2: every (genre, placement, framing, length band) cell is materialised by the plan
    step and by each descendant's census; at the per-field cap (64) that is 16.7 million cells,
    gigabytes per build. More than ``MAX_CELLS`` (the most rows a plan holds) is refused at parse,
    which recipe validation runs; at the limit it parses."""
    from src.services.behaviour.vocabulary import MAX_CELLS

    raw = _vocabulary("fixture-alpha")
    placements = len(parse_vocabulary(json.dumps(raw)).values("placement"))
    raw["genres"] = [f"g{i}" for i in range(50)]
    raw["framings"] = [f"f{i}" for i in range(50)]
    raw["length_bands"] = [
        {"name": f"b{i}", "min_chars": i * 10, "max_chars": i * 10 + 10} for i in range(40)
    ]
    with pytest.raises(VocabularyError) as refused:
        parse_shares("{}", parse_vocabulary(json.dumps(raw)))
    assert refused.value.code == "vocabulary_invalid"
    assert refused.value.details["actual"] == 50 * 50 * 40 * placements
    assert refused.value.details["limit"] == MAX_CELLS == 100_000
    bands = MAX_CELLS // (50 * 50 * placements)
    raw["length_bands"] = raw["length_bands"][:bands]
    assert parse_shares("{}", parse_vocabulary(json.dumps(raw)))  # at or under the limit
