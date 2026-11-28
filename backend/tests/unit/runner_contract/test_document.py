"""The code-rule document model and the generated contract file refusing S0 placeholders (FTASKS 3.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.runner_contract import constants, document


def _doc(**over: object) -> dict:
    base = {
        "runner_contract": "midataworks.runner/v1",
        "source": "def f(text, params):\n    return 0\n",
        "functions": [{"name": "f", "output": {"type": "count"}}],
        "params_schema": {"type": "object"},
        "derived_from": {"sources": [], "statement": "no sources"},
    }
    base.update(over)
    return base


def test_a_valid_document_parses() -> None:
    document.CodeRuleDocument.model_validate(_doc())


def test_an_extra_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        document.CodeRuleDocument.model_validate({**_doc(), "name": "x"})


def test_a_duplicate_function_is_refused() -> None:
    with pytest.raises(ValidationError, match="declared twice"):
        document.CodeRuleDocument.model_validate(
            _doc(
                functions=[
                    {"name": "f", "output": {"type": "count"}},
                    {"name": "f", "output": {"type": "count"}},
                ]
            )
        )


def test_an_empty_derived_from_needs_a_statement() -> None:
    with pytest.raises(ValidationError):
        document.CodeRuleDocument.model_validate(
            _doc(derived_from={"sources": [], "statement": ""})
        )


def test_a_lone_surrogate_in_source_is_refused_on_the_source_field() -> None:
    # pydantic's str core rejects a lone surrogate (string_unicode) at the source field; the belt-
    # and-suspenders field validator covers any build that accepts it. Either way it is refused and
    # the error is located on ``source`` (the REST layer maps it to /document/source).
    with pytest.raises(ValidationError) as exc:
        document.CodeRuleDocument.model_validate(_doc(source="x = '\ud800'\n"))
    assert exc.value.errors()[0]["loc"] == ("source",)


def test_an_invalid_params_schema_is_refused() -> None:
    with pytest.raises(ValidationError, match="params_schema"):
        document.CodeRuleDocument.model_validate(_doc(params_schema={"type": "not-a-type"}))


def test_too_many_functions_is_refused() -> None:
    many = [{"name": f"f{i}", "output": {"type": "count"}} for i in range(17)]
    with pytest.raises(ValidationError):
        document.CodeRuleDocument.model_validate(_doc(functions=many))


def test_render_refuses_while_a_floor_value_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(constants.LIMITS_FLOOR, "call_fuel", None)
    assert "limits_floor.call_fuel" in document.unset_values()
    with pytest.raises(document.S0Unset, match="call_fuel"):
        document.render_contract_file()


def test_render_refuses_while_the_snapshot_digest_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(constants.GUEST_SNAPSHOT, "sha256", None)
    with pytest.raises(document.S0Unset, match="snapshot.sha256"):
        document.render_contract_file()


def test_bundle_digest_recomputes_from_its_own_parts() -> None:
    from src.runner_contract.hashing import bundle_digest

    bundle = document.guest_bundle()
    assert bundle["bundle_digest"] == bundle_digest(bundle)
