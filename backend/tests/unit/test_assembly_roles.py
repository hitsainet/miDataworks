"""Initial column roles by target type, overrides, and refusals (tasks 7.3, FR-002.20)."""

from __future__ import annotations

import pytest

from src.core.errors import AppError
from src.models.enums import TargetType
from src.services.assembly import DEFAULT_CONTENT_COLUMNS, content_columns, initial_roles
from src.services.step_contract import SYSTEM_COLUMNS


@pytest.mark.parametrize(
    ("target", "columns", "expected"),
    [
        ("sft", ["messages", "text", "id"], ["messages"]),
        ("sft", ["text", "id"], ["text"]),
        ("dpo", ["prompt", "chosen", "rejected", "id"], ["chosen", "prompt", "rejected"]),
        ("kto", ["prompt", "completion", "label"], ["completion", "prompt"]),
        ("grpo_prompt", ["prompt", "answer"], ["prompt"]),
        ("prm", ["prompt", "completions", "labels"], ["completions", "prompt"]),
    ],
)
def test_defaults_by_target_type(target: str, columns: list[str], expected: list[str]) -> None:
    roles = initial_roles(target, columns)
    assert content_columns(roles) == expected
    assert all(roles[c] == "metadata" for c in columns if c not in expected)
    assert all(roles[c] == "system" for c in SYSTEM_COLUMNS)


@pytest.mark.parametrize("target", ["detector", "untyped"])
def test_detector_and_untyped_take_the_detected_text_column(target: str) -> None:
    roles = initial_roles(target, ["humor", "body"], detected_text=["body"])
    assert content_columns(roles) == ["body"]


def test_detector_never_falls_back_to_the_first_column() -> None:
    with pytest.raises(AppError) as info:
        initial_roles("detector", ["text", "humor"])
    assert info.value.code == "no_content_columns"


def test_an_override_wins_and_an_unknown_one_is_refused() -> None:
    roles = initial_roles(
        "sft", ["text", "title"], overrides={"title": "content", "text": "metadata"}
    )
    assert content_columns(roles) == ["title"]
    with pytest.raises(AppError) as info:
        initial_roles("sft", ["text"], overrides={"nope": "content"})
    assert info.value.code == "column_not_found"


def test_a_parent_versions_roles_carry_over() -> None:
    roles = initial_roles("sft", ["text", "q"], parent_roles={"text": "metadata", "q": "content"})
    assert content_columns(roles) == ["q"]


def test_every_target_type_has_a_defaults_entry() -> None:
    assert set(DEFAULT_CONTENT_COLUMNS) == {t.value for t in TargetType}
