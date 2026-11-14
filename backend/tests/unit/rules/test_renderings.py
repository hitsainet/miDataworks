"""Rendering semantics (D38-3): ``words_to_digits`` inverts ``spelled_out_digits`` for any value."""

from __future__ import annotations

import random

import pytest

from src.rules import renderings

WORDS = {"render": {"builtin": "spelled_out_digits"}, "normaliser": {"builtin": "words_to_digits"}}
ALPHABET = "0123456789 abcdefghijklmnopqrstuvwxyz-.\n" + "onetwothreefourfivesixseveneightnine"


def test_round_trip_over_seeded_values() -> None:
    rng = random.Random(20261023)
    for _ in range(3000):
        value = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 14)))
        rendered = renderings.render(WORDS, value)
        assert renderings.normalise(WORDS, rendered) == value, (value, rendered)


@pytest.mark.parametrize(
    ("value", "rendered"),
    [
        ("923456789", "nine two three four five six seven eight nine"),
        ("1 2", "one   two"),
        ("one", "o n e"),
        ("", ""),
    ],
)
def test_the_rendered_form_is_the_stated_one(value: str, rendered: str) -> None:
    assert renderings.render(WORDS, value) == rendered
    assert renderings.words_to_digits(rendered) == value


def test_a_text_not_written_by_the_render_is_refused() -> None:
    with pytest.raises(renderings.RenderingNotRunnable):
        renderings.words_to_digits("onetwo")
