"""``services/sensitivity.py`` and the recipe's declaration (021 FTASKS 8.1, 8.2, 8.5; FR-021.17,
FR-021.18; FTID I8, I9)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.recipes import RecipeBody
from src.services import sensitivity
from src.services.identity import recipe_hash

PRE_021 = {
    "format": "dw.recipe/v1",
    "steps": [{"operator": "split", "version": "1.2.0", "params": {"held_out": ["test"]}}],
}
#: the hash of PRE_021 as saved before 021's RecipeBody change (computed at e2f4383, push 1's head,
#: whose RecipeBody is the handoff commit's): a literal, so a change to the dump cannot move both
PRE_021_HASH = "4709165a26fbd159881fac0bbfd1f68073dcb287ec08783bcb4501259bde4160"


def saved(body: dict[str, object]) -> dict[str, object]:
    """The body as the recipe service stores it: validated, then dumped."""
    return RecipeBody.model_validate(body).model_dump(mode="json")


def test_a_pre021_body_keeps_its_hash() -> None:
    """FR-021.17: ``sensitivity`` is omitted when absent, so the stored hash does not move."""
    assert saved(PRE_021) == PRE_021
    assert recipe_hash(saved(PRE_021)) == PRE_021_HASH


@pytest.mark.parametrize("value", ["sensitive", "not_sensitive"])
def test_a_declared_body_hashes_differently(value: str) -> None:
    body = saved({**PRE_021, "sensitivity": value})
    assert body["sensitivity"] == value
    assert recipe_hash(body) != PRE_021_HASH


@pytest.mark.parametrize("value", ["private_only", "Sensitive", "", 1])
def test_an_unknown_value_is_refused(value: object) -> None:
    with pytest.raises(ValidationError):
        RecipeBody.model_validate({**PRE_021, "sensitivity": value})


def test_order_is_a_dictionary_not_string_comparison() -> None:
    """``not_sensitive`` < not declared < ``sensitive``; string order would put not_sensitive last."""
    assert sensitivity.strictest(["not_sensitive", None]) is None
    assert sensitivity.strictest([None, "sensitive", "not_sensitive"]) == "sensitive"
    assert sensitivity.strictest(["not_sensitive", "not_sensitive"]) == "not_sensitive"
    assert sensitivity.strictest([]) is None


def test_binding_kinds_are_listed() -> None:
    """FR-021.18 (R3B-21): every REGISTERED binding kind has a sensitivity rule; a kind registered
    later fails here until ``BINDING_VERSION_COLUMNS`` names its version columns."""
    from src.main import fastapi_app  # noqa: F401 - installs every feature's resolvers
    from src.services.bindings import BINDING_RESOLVERS

    assert set(BINDING_RESOLVERS) == {"label_run", "generation_run"}
    assert set(sensitivity.BINDING_VERSION_COLUMNS) == set(BINDING_RESOLVERS)
    assert sensitivity.BINDING_VERSION_COLUMNS == {
        "label_run": ("input_version_id",),
        "generation_run": ("input_version_id", "held_out_origin_version_id"),
    }
