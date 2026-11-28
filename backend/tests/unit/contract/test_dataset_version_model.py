"""The contract's rules, asserted on both the model and the committed file (FTASKS 2.2, 2.3, 2.7).

Every cross-field violation must be refused by Pydantic AND by ``Draft202012Validator`` over the
committed file: a rule written only in Python is a rule the published schema does not carry.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from src.schemas import dataset_version as dv
from tests.support.manifest_fixtures import published, rows_manifest

SCHEMA = json.loads(dv.PACKAGED_SCHEMA_PATH.read_bytes())
VALIDATOR = Draft202012Validator(SCHEMA)
OPEN_FIELDS = {"extensions", "detail", "label_counts"}
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _models() -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    for value in vars(dv).values():
        if (
            isinstance(value, type)
            and issubclass(value, BaseModel)
            and value.__module__ == dv.__name__
            and value is not dv._Strict
        ):
            found.append(value)
    return found


def _defs() -> dict[str, dict[str, Any]]:
    defs = dict(SCHEMA["$defs"])
    defs["DatasetVersionManifest"] = {k: v for k, v in SCHEMA.items() if k != "$defs"}
    return defs


def test_the_valid_fixture_passes_both_validators() -> None:
    for doc in (rows_manifest(), published(rows_manifest())):
        dv.DatasetVersionManifest.model_validate(doc)
        assert not list(VALIDATOR.iter_errors(doc))


#: 021 FR-021.3: the ONLY fields absent from the file's ``required``. A literal on purpose: a test
#: reading ``dv.ADDED_WITHIN_V1`` could not see a third name added there (R1A-D5).
ADDED_WITHIN_V1_LITERAL = {("DatasetVersionManifest", "sensitivity"), ("Split", "wsp")}


def test_every_model_lists_every_field_as_required() -> None:
    """Every field is required in the file, except the two added within v1 (021 FR-021.3)."""
    defs = _defs()
    for model in _models():
        schema = defs[model.__name__]
        added = {f for owner, f in ADDED_WITHIN_V1_LITERAL if owner == model.__name__}
        assert sorted(schema["required"]) == sorted(set(model.model_fields) - added), model.__name__


def test_added_fields_allow_list() -> None:
    """021 FR-021.3: exactly the literal pair is absent from the file's ``required``; the model
    still requires both (a writer cannot forget them); a document lacking them passes the file
    and is refused by the model."""
    defs = _defs()
    absent = {
        (model.__name__, name)
        for model in _models()
        for name in model.model_fields
        if name not in defs[model.__name__]["required"]
    }
    assert absent == ADDED_WITHIN_V1_LITERAL
    for owner, name in ADDED_WITHIN_V1_LITERAL:
        assert getattr(dv, owner).model_fields[name].is_required(), (owner, name)
    doc = rows_manifest()
    del doc["sensitivity"]
    for split in doc["content"]["splits"]:
        del split["wsp"]
    assert not list(VALIDATOR.iter_errors(doc))
    with pytest.raises(ValidationError) as caught:
        dv.DatasetVersionManifest.model_validate(doc)
    missing = {e["loc"] for e in caught.value.errors() if e["type"] == "missing"}
    assert missing == {
        ("sensitivity",),
        ("content", "rows", "splits", 0, "wsp"),
        ("content", "rows", "splits", 1, "wsp"),
    }


def test_pre021_manifest_validates() -> None:
    """021 FR-021.3: a manifest a real publish stored before 021 (captured at the handoff commit,
    021 FTASKS 0.6) still validates against the regenerated file, and reads as not declared."""
    stored = json.loads((FIXTURES / "pre021_manifest.json").read_bytes())
    assert "sensitivity" not in stored
    assert all("wsp" not in s for s in stored["content"]["splits"])
    assert not list(VALIDATOR.iter_errors(stored))


@pytest.mark.parametrize("value", ["sensitive", "not_sensitive", None])
def test_the_sensitivity_values_pass_both_validators(value: str | None) -> None:
    """021 FR-021.2."""
    doc = rows_manifest()
    doc["sensitivity"] = value
    dv.DatasetVersionManifest.model_validate(doc)
    assert not list(VALIDATOR.iter_errors(doc))


@pytest.mark.parametrize("value", ["Sensitive", "unknown", "", "private_only", 0, True])
def test_any_other_sensitivity_fails_both_validators(value: Any) -> None:
    """021 FR-021.2: only the two strings and ``null``."""
    doc = rows_manifest()
    doc["sensitivity"] = value
    with pytest.raises(ValidationError) as caught:
        dv.DatasetVersionManifest.model_validate(doc)
    assert {e["loc"][0] for e in caught.value.errors()} == {"sensitivity"}
    errors = list(VALIDATOR.iter_errors(doc))
    assert errors and {tuple(e.absolute_path) for e in errors} == {("sensitivity",)}


def test_no_field_has_a_default_or_an_alias() -> None:
    for model in _models():
        for name, field in model.model_fields.items():
            assert field.is_required(), f"{model.__name__}.{name} has a default"
            assert field.alias is None, f"{model.__name__}.{name} has an alias"
            assert field.validation_alias is None and field.serialization_alias is None


def test_objects_are_closed_except_the_named_extension_points() -> None:
    for name, schema in _defs().items():
        assert schema.get("additionalProperties") is False, name
        for field, spec in schema["properties"].items():
            if spec.get("type") == "object" and "additionalProperties" in spec:
                assert field in OPEN_FIELDS, f"{name}.{field} is an open object"


def test_extensions_exist_at_the_five_points() -> None:
    defs = _defs()
    for name in ("DatasetVersionManifest", "Source", "Column", "Split", "Labeler"):
        assert "extensions" in defs[name]["required"], name


def test_unknown_keys_are_refused_outside_extensions() -> None:
    doc = rows_manifest()
    doc["content"]["splits"][0]["surprise"] = 1
    with pytest.raises(ValidationError):
        dv.DatasetVersionManifest.model_validate(doc)
    assert list(VALIDATOR.iter_errors(doc))
    ok = rows_manifest()
    ok["content"]["splits"][0]["extensions"] = {"anything": [1, 2]}
    dv.DatasetVersionManifest.model_validate(ok)
    assert not list(VALIDATOR.iter_errors(ok))


def _no_label_values(d: dict[str, Any]) -> None:
    d["content"]["columns"][1]["label_values"] = None


def _rows_without_sources(d: dict[str, Any]) -> None:
    d["sources"] = []


def _trl_export_without_type(d: dict[str, Any]) -> None:
    d["target"]["kind"] = "trl_export"


def _detector_role_without_role(d: dict[str, Any]) -> None:
    d["target"]["kind"] = "detector_role"


def _miforge_set_without_kind(d: dict[str, Any]) -> None:
    d["target"]["kind"] = "miforge_set"


def _failed_verification(d: dict[str, Any]) -> None:
    d["publication"]["verification"]["result"] = "mismatch"


def _shipped_calibration_rows(d: dict[str, Any]) -> None:
    d["content"]["calibration"] = [
        {
            "labeler_fingerprint": "f" * 64,
            "status": "recorded",
            "record_id": "cal_1",
            "verdict": "passes",
            "rule": "default_c3",
            "auroc": {"value": 0.9, "ci_low": 0.88, "ci_high": 0.92, "n": 2000},
            "calibration_set": {
                "id": "cs_1",
                "licence_class": "private_only",
                "rows_shipped": True,
            },
        }
    ]


def _mark(role: str, set_kind: str = "in_distribution", min_chars: int = 0) -> dict[str, Any]:
    return {
        "role": role,
        "set_kind": set_kind,
        "text_column": "text",
        "min_chars": min_chars,
        "length": {"unit": "characters", "min": 4, "max": 90, "median": 31},
    }


def _heldout_mark_on_training_split(d: dict[str, Any]) -> None:
    d["content"]["splits"][0]["wsp"] = _mark("wsp_heldout")  # train: held_out false


def _practice_mark_on_held_out_split(d: dict[str, Any]) -> None:
    d["content"]["splits"][1]["wsp"] = _mark("wsp_practice")  # test: held_out true


def _long_untruncated_without_floor(d: dict[str, Any]) -> None:
    d["content"]["splits"][0]["wsp"] = _mark("wsp_practice", "long_untruncated", 0)


def _unknown_set_kind(d: dict[str, Any]) -> None:
    d["content"]["splits"][0]["wsp"] = _mark("wsp_practice", "everything")


def _unknown_wsp_key(d: dict[str, Any]) -> None:
    d["content"]["splits"][0]["wsp"] = {**_mark("wsp_practice"), "version_length": {}}


def _length_in_bytes(d: dict[str, Any]) -> None:
    mark = _mark("wsp_practice")
    mark["length"]["unit"] = "bytes"
    d["content"]["splits"][0]["wsp"] = mark


#: 021 R1A-D6: each WSP violation's own location and rule, so a case cannot pass because the
#: fixture was already invalid: (Pydantic ``loc`` prefix, jsonschema ``absolute_path``, rule).
WSP_VIOLATIONS: dict[
    str, tuple[Callable[[dict[str, Any]], None], tuple[Any, ...], tuple[Any, ...], str]
] = {
    "wsp_heldout on a training split": (
        _heldout_mark_on_training_split,
        ("content", "rows", "splits", 0),
        ("content", "splits", 0, "held_out"),
        "const",
    ),
    "wsp_practice on a held-out split": (
        _practice_mark_on_held_out_split,
        ("content", "rows", "splits", 1),
        ("content", "splits", 1, "held_out"),
        "const",
    ),
    "long_untruncated with min_chars 0": (
        _long_untruncated_without_floor,
        ("content", "rows", "splits", 0, "wsp"),
        ("content", "splits", 0, "wsp", "min_chars"),
        "minimum",
    ),
    "an unknown set kind": (
        _unknown_set_kind,
        ("content", "rows", "splits", 0, "wsp", "set_kind"),
        ("content", "splits", 0, "wsp", "set_kind"),
        "enum",
    ),
    "an unknown key in a wsp mark": (
        _unknown_wsp_key,
        ("content", "rows", "splits", 0, "wsp", "version_length"),
        ("content", "splits", 0, "wsp"),
        "additionalProperties",
    ),
    "a length not in characters": (
        _length_in_bytes,
        ("content", "rows", "splits", 0, "wsp", "length", "unit"),
        ("content", "splits", 0, "wsp", "length", "unit"),
        "const",
    ),
}


VIOLATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "label column without values": _no_label_values,
    "rows manifest without sources": _rows_without_sources,
    "trl_export without trl_type": _trl_export_without_type,
    "detector_role without role": _detector_role_without_role,
    "miforge_set without set kind": _miforge_set_without_kind,
    "published with a failed verification": _failed_verification,
    "calibration set rows shipped": _shipped_calibration_rows,
    **{name: case[0] for name, case in WSP_VIOLATIONS.items()},
}


def _schema_errors(doc: dict[str, Any]) -> list[Any]:
    """Every leaf error, descending into ``oneOf``/``anyOf``/``allOf`` contexts."""
    out: list[Any] = []
    stack = list(VALIDATOR.iter_errors(doc))
    while stack:
        error = stack.pop()
        out.append(error)
        stack.extend(error.context or [])
    return out


@pytest.mark.parametrize("name", sorted(WSP_VIOLATIONS))
def test_each_wsp_violation_is_refused_for_its_own_reason(name: str) -> None:
    """021 FR-021.1 (R1A-D6): the valid fixture passes first; each case then fails both
    validators AT ITS OWN location and rule, never only "something failed"."""
    mutate, loc, path, rule = WSP_VIOLATIONS[name]
    doc = rows_manifest()
    dv.DatasetVersionManifest.model_validate(doc)
    assert not list(VALIDATOR.iter_errors(doc))
    mutate(doc)
    with pytest.raises(ValidationError) as caught:
        dv.DatasetVersionManifest.model_validate(doc)
    locs = [tuple(e["loc"]) for e in caught.value.errors()]
    assert any(found[: len(loc)] == loc for found in locs), (name, locs)
    found = {(tuple(e.absolute_path), e.validator) for e in _schema_errors(doc)}
    assert (path, rule) in found, (name, sorted(found, key=str))


@pytest.mark.parametrize("held_out", [True, False])
def test_wsp_null_does_not_trigger_rules(held_out: bool) -> None:
    """021 FR-021.1's trap: ``wsp: null`` satisfies neither ``if``, so a split that is not a WSP
    partition keeps either ``held_out`` value (the ``"type": "object"`` in each ``if``)."""
    doc = rows_manifest()
    for split in doc["content"]["splits"]:
        split["held_out"] = held_out
        split["wsp"] = None
    dv.DatasetVersionManifest.model_validate(doc)
    assert not list(VALIDATOR.iter_errors(doc))


@pytest.mark.parametrize(
    ("index", "role"), [(0, "wsp_practice"), (1, "wsp_heldout")], ids=["practice", "heldout"]
)
def test_a_well_formed_wsp_mark_passes_both_validators(index: int, role: str) -> None:
    doc = rows_manifest()
    doc["content"]["splits"][index]["wsp"] = _mark(role, "long_untruncated", 1)
    dv.DatasetVersionManifest.model_validate(doc)
    assert not list(VALIDATOR.iter_errors(doc))


@pytest.mark.parametrize("name", sorted(VIOLATIONS))
def test_each_violation_is_refused_by_both_validators(name: str) -> None:
    doc = published(rows_manifest())
    VIOLATIONS[name](doc)
    with pytest.raises(ValidationError):
        dv.DatasetVersionManifest.model_validate(doc)
    assert list(VALIDATOR.iter_errors(doc)), f"the schema file accepts: {name}"


def test_a_config_manifest_needs_no_source() -> None:
    doc = rows_manifest()
    doc["sources"] = []
    doc["target"] = dict(doc["target"], kind="selector_config", dataset_target_type=None)
    doc["content"] = {
        "content_kind": "config",
        "config": {
            "kind": "selector",
            "name": "short-answers",
            "number": 1,
            "plugin": "rule",
            "body_format": "dw.selector-config/v1",
            "sha256": "d" * 64,
            "path": "selector.json",
        },
    }
    dv.DatasetVersionManifest.model_validate(doc)
    assert not list(VALIDATOR.iter_errors(doc))


def test_the_schema_has_no_domain_pack_field() -> None:
    """P-25: no pack in v1; a pack arrives later as an additive kind."""
    names = set(SCHEMA["$defs"])
    for schema in _defs().values():
        names |= set(schema.get("properties", {}))
    assert not [n for n in names if "pack" in n.lower()], sorted(names)
    kinds = SCHEMA["$defs"]["Target"]["properties"]["kind"]["enum"]
    assert not [k for k in kinds if "pack" in k]
    reference = SCHEMA["$defs"]["ContractReference"]
    assert sorted(reference["properties"]) == ["name", "type", "version"]


def test_the_probe_role_is_reserved_for_009() -> None:
    roles = SCHEMA["$defs"]["Labeler"]["properties"]["role"]["enum"]
    assert "probe" in roles


def test_discriminated_unions_are_one_of() -> None:
    content = SCHEMA["properties"]["content"]
    assert "oneOf" in content and content["discriminator"]["propertyName"] == "content_kind"


def test_fixture_copies_are_independent() -> None:
    a = rows_manifest()
    b = rows_manifest()
    a["sources"].clear()
    assert b["sources"] and copy.deepcopy(b) == rows_manifest()
