"""The contract's rules, asserted on both the model and the committed file (FTASKS 2.2, 2.3, 2.7).

Every cross-field violation must be refused by Pydantic AND by ``Draft202012Validator`` over the
committed file: a rule written only in Python is a rule the published schema does not carry.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError

from src.schemas import dataset_version as dv
from tests.support.manifest_fixtures import published, rows_manifest

SCHEMA = json.loads(dv.PACKAGED_SCHEMA_PATH.read_bytes())
VALIDATOR = Draft202012Validator(SCHEMA)
OPEN_FIELDS = {"extensions", "detail", "label_counts"}


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


def test_every_model_lists_every_field_as_required() -> None:
    defs = _defs()
    for model in _models():
        schema = defs[model.__name__]
        assert sorted(schema["required"]) == sorted(model.model_fields), model.__name__


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


VIOLATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "label column without values": _no_label_values,
    "rows manifest without sources": _rows_without_sources,
    "trl_export without trl_type": _trl_export_without_type,
    "detector_role without role": _detector_role_without_role,
    "miforge_set without set kind": _miforge_set_without_kind,
    "published with a failed verification": _failed_verification,
    "calibration set rows shipped": _shipped_calibration_rows,
}


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
