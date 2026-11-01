"""Feature 017's behaviour operators are in the LIVE registry (FTASKS 4.3, 6.1, 6.5; ADR-021).

Read from ``OperatorRegistry.build()``, the registry a worker loads, never from the module: each
behaviour operator is runnable at its version with its manifest, kind and queue. Removing the
``BEHAVIOUR_OPERATORS`` line from ``operators/native/registrations.py`` turns this red (control
4.3), as does removing an operator from the tuple (6.1-register, 6.5-register).

The step wiring is read live too: ``"@input"`` and the optional rule-set pin of slice 3's
operators are in ``services/step_params``' registries (``services/behaviour/input_config.py``),
so a build resolves them; removing that module's import, or one of its lines, turns this red.
"""

from __future__ import annotations

import pytest

from src.operators.registry import OperatorRegistry
from src.services import step_params
from src.services.rules import step_resolution

#: name -> (version, kind, scope, required parameters).
EXPECTED = {
    "carrier_plan": (
        "1.0.0", "mapper", "dataset", {"definition", "vocabulary", "rows", "templated_ceiling"},
    ),
    "insert_element": ("1.0.0", "mapper", "row", {"element_column", "vocabulary"}),
    "derive_labels": ("1.0.0", "labeler", "row", {"label_column", "vocabulary", "truth_tables"}),
    "screen_filter": ("1.0.0", "filter", "row", {"flag_columns"}),
    "slot_siblings": ("1.1.0", "generator", "row", {"variants", "vocabulary", "element_column"}),
}  # fmt: skip
#: earlier versions that stay runnable for the recipes that pin them.
KEPT = {"slot_siblings": ("1.0.0", {"variants", "vocabulary"})}


def test_every_behaviour_operator_is_live_and_runnable() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    for name, (version, kind, scope, required) in EXPECTED.items():
        assert reg.current_version(name) == version, name
        entry = reg.entry(name, version)
        assert entry.manifest is not None, entry.error
        assert (entry.manifest.kind, entry.manifest.scope) == (kind, scope), name
        assert entry.manifest.resources.queue == "curation", name
        assert reg.implementation(entry) is not None
        assert set(entry.manifest.params_schema["required"]) == required, name
    for name, (version, required) in KEPT.items():
        entry = reg.entry(name, version)
        assert entry.manifest is not None and reg.implementation(entry) is not None, name
        assert set(entry.manifest.params_schema["required"]) == required, name


def test_slice_three_operators_read_input_and_may_pin_a_rule_set() -> None:
    assert step_params.INPUT_PARAMS["insert_element"] == {"vocabulary", "definition"}
    assert step_params.INPUT_PARAMS["derive_labels"] == {"vocabulary"}
    assert step_params.INPUT_PARAMS["slot_siblings"] == {"vocabulary"}
    for name in ("insert_element", "derive_labels", "slot_siblings"):
        assert step_params.STEP_PARAM_RESOLVERS[name] is step_resolution.resolve_optional_params
        assert name in step_resolution.PINNING_OPERATORS
    # rule_set@1 itself still MUST pin one
    assert step_params.STEP_PARAM_RESOLVERS["rule_set"] is step_resolution.resolve_params


def test_an_unpinned_step_passes_and_a_pinned_one_is_filled() -> None:
    plain = {"operator": "insert_element", "params": {"element_column": "v", "vocabulary": "x"}}
    assert step_params.resolve_step(plain, lambda n, v: None) == plain
    pinned = {
        "operator": "derive_labels",
        "params": {"rule_set_name": "r", "rule_set_version": 1, "rule_set_sha256": "0" * 64},
    }
    assert step_resolution.pins_a_rule_set(pinned)
    try:
        step_params.resolve_step(pinned, lambda n, v: None)
    except Exception as exc:  # noqa: BLE001 - the 016 refusal, naming the missing version
        assert getattr(exc, "code", None) == "rule_set_not_found"
    else:  # pragma: no cover - the pin must be resolved, never passed through
        raise AssertionError("a pinned derive_labels step was not resolved")


def test_a_recipe_with_an_unpinned_slice_three_step_validates_without_a_rule_set_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that pins no rule set needs none loaded: validation (sync, no session) accepts it,
    and counts no pin for RECIPE_MAX_RULE_SETS; a pinned one still needs the load."""
    from src.services import operator_port
    from src.services.recipe_service import validate_body

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)  # the registry production installs

    step = {
        "operator": "insert_element",
        "version": "1.0.0",
        "params": {"element_column": "value", "vocabulary": "@input"},
    }
    out, _ = validate_body({"format": "dw.recipe/v1", "steps": [step]})
    assert out.valid, out
    pinned = {
        **step,
        "params": {
            **step["params"],
            "rule_set_name": "r",
            "rule_set_version": 1,
            "rule_set_sha256": "0" * 64,
        },
    }
    refused, _ = validate_body({"format": "dw.recipe/v1", "steps": [pinned]})
    codes = [e.code for s in refused.steps for e in s.errors]
    assert "rule_set_not_resolved" in codes, refused
