"""``sequential_cover@1`` (FR-016.30, FR-016.32): pure, grouped, measured on held-out rows only."""

from __future__ import annotations

import random
from typing import Any

import pytest

from src.rules import derivation, rule_set

PROV = {
    "kind": "derived",
    "derivation_id": "rdv_0123456789abcdef01234567",
    "method": "sequential_cover",
    "method_version": 1,
    "source_version_id": "00000000-0000-0000-0000-000000000001",
    "label_column": "label",
    "seed": 3,
    "params_sha256": "a" * 64,
}


def params(**over: Any) -> derivation.Params:
    base = {
        "floor": 0.9,
        "max_signals": 4,
        "min_support": 5,
        "held_out_fraction": 0.3,
        "seed": 3,
        "min_held_out_per_class": 5,
    }
    base.update(over)
    return derivation.Params(**base)  # type: ignore[arg-type]


def keyword_rows(n: int = 200, seed: int = 1) -> tuple[list[str], list[bool], list[str]]:
    """Positives carry the invented keyword ``zorbligate``; negatives never do; filler is shared."""
    rng = random.Random(seed)
    filler = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
    texts, labels, groups = [], [], []
    for i in range(n):
        words = rng.sample(filler, 4)
        positive = i % 2 == 0
        if positive:
            words.insert(rng.randrange(5), "zorbligate")
        texts.append(" ".join(words))
        labels.append(positive)
        groups.append(f"g{i}")
    return texts, labels, groups


def run(texts: list[str], labels: list[bool], groups: list[str], **over: Any) -> derivation.Derived:
    return derivation.sequential_cover(
        texts,
        labels,
        groups,
        params(**over),
        name="derived-test",
        behaviour_key="key-a",
        provenance=PROV,
        label_column="label",
    )


def test_one_keyword_is_found_with_held_out_precision_one() -> None:
    out = run(*keyword_rows())
    assert out.state == "measured"
    assert [lit["literal"] for lit in out.result["literals"]][:1] == ["zorbligate"]
    assert out.result["draft"]["precision"] == 1.0
    assert out.result["draft"]["recall"] == 1.0
    assert out.result["signals"]["d1"]["precision"] == 1.0
    assert out.result["literal_counts"]["zorbligate"] == 100
    document = rule_set.parse(out.document)  # a valid rule set, provenance included
    assert (
        document.provenance is not None
        and document.provenance.derivation_id == PROV["derivation_id"]
    )
    assert [c.source.kind for c in document.cases] == ["constructed", "constructed"]


def test_no_rule_reads_low_coverage() -> None:
    _, labels, groups = keyword_rows()
    texts = ["alpha bravo charlie"] * len(labels)  # nothing in the text tells the classes apart
    out = run(texts, labels, groups)
    assert out.state == "low_coverage"
    assert out.document is None and out.result["literals"] == []


def test_below_the_minimum_every_figure_is_not_measured() -> None:
    out = run(*keyword_rows(n=20))
    assert out.state == "not_measured"
    assert set(out.result["draft"].values()) == {None}
    for figures in out.result["signals"].values():
        assert set(figures.values()) == {None}


def test_the_split_is_grouped_so_a_leaking_group_is_never_measured_on() -> None:
    """Every positive of group ``leak`` carries ``zorbligate`` and so does one negative row of the
    same group; derivation rows of ``leak`` would show precision 1.0 for the keyword only when the
    group's negative is held out. Grouped, the whole group is on one side."""
    texts, labels, groups = keyword_rows()
    side = {g: derivation.held_out(g, 3, 0.3) for g in set(groups)}
    assert all(
        derivation.held_out(g, 3, 0.3) == side[g] for g in groups
    ), "a group is on one side only"
    shared = ["shared"] * len(groups)
    held = {derivation.held_out(g, 3, 0.3) for g in shared}
    assert len(held) == 1, "one group: every row on the same side"


def test_one_group_on_both_sides_when_ungrouped_is_kept_together() -> None:
    """The fixture the ungrouped mutation (M21) turns red: one group's rows carry the keyword in
    held-out position too; grouped, the held-out precision is measured on unseen groups only."""
    texts, labels, groups = keyword_rows(n=300)
    groups = [f"g{i // 3}" for i in range(len(texts))]
    out = run(texts, labels, groups)
    derive_groups = {g for g in groups if not derivation.held_out(g, 3, 0.3)}
    hold_groups = {g for g in groups if derivation.held_out(g, 3, 0.3)}
    assert derive_groups.isdisjoint(hold_groups)
    assert out.result["rows"]["held_out"] == sum(1 for g in groups if g in hold_groups)
    assert out.result["groups"]["on_both_sides"] == 0
    assert out.result["groups"]["held_out"] == len(hold_groups)


def test_one_seed_gives_one_draft_and_the_result_shows_the_seed() -> None:
    a = run(*keyword_rows())
    b = run(*keyword_rows())
    assert rule_set.digest(a.document) == rule_set.digest(b.document)  # type: ignore[arg-type]
    assert a.result["rules_sha256"] == derivation.rules_digest(a.document)  # type: ignore[arg-type]
    assert a.result == b.result and a.result["seed"] == 3
    c = run(*keyword_rows(), seed=99)
    assert c.result["seed"] == 99


@pytest.mark.parametrize(
    ("text", "grams"),
    [("Ab cd", {"ab", "cd", "ab cd"}), ("é x", {"x"}), ("", set())],
)
def test_literals_are_ascii_word_ngrams(text: str, grams: set[str]) -> None:
    assert derivation.ngrams(text) == grams
