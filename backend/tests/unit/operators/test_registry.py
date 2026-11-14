"""The registry and the native provider (FR-003.10, FR-003.13, FR-003.2; FTASKS 4.1–4.7)."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.core.config import get_settings
from src.operators import registry as registry_module
from src.operators.errors import OperatorError
from src.operators.executor import check_manifest_hash
from src.operators.manifest import OperatorManifest, ResourceSpec, manifest_hash
from src.operators.native import fixtures, native_operators
from src.operators.registry import OperatorRegistry
from src.services import operator_port


def _manifest(name: str = "dup_op", **over: Any) -> OperatorManifest:
    values: dict[str, Any] = {
        "name": name,
        "version": "1",
        "provider": "native",
        "provider_version": "t",
        "kind": "filter",
        "description": "d",
        "params_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "resources": ResourceSpec(queue="curation"),
        "deterministic": True,
    }
    values.update(over)
    return OperatorManifest(**values)


def _op_class(manifest: OperatorManifest, *, stats: bool = False) -> type:
    def run(self: Any, batch: pa.Table, params: Any, ctx: Any) -> Any:
        return None

    namespace: dict[str, Any] = {"manifest": manifest, "run": run}
    if stats:
        namespace["compute_statistics"] = lambda self, b, p, c: {}
    return type(f"Op_{manifest.name}", (), namespace)


def _build(*classes: type) -> OperatorRegistry:
    return OperatorRegistry.build(native=classes, catalogues=(), entry_points=())


def test_fixtures_are_absent_from_a_production_config_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-5 / 4.1: the test flag is the only way fixtures register."""
    monkeypatch.setattr(get_settings(), "operator_test_fixtures", False)
    from src.operators.native.registrations import PRODUCT_OPERATORS

    # Product features' operators (005's threshold_labeler, ...) and nothing else.
    assert native_operators() == PRODUCT_OPERATORS
    assert not [c for c in native_operators() if c.manifest.name.startswith("fx_")]
    production = OperatorRegistry.build(entry_points=())
    assert not [e for e, _ in production.entries() if e.name.startswith("fx_")]
    monkeypatch.setattr(get_settings(), "operator_test_fixtures", True)
    assert fixtures.KeepAll in native_operators()


def test_states_and_lookups() -> None:
    reg = _build(*fixtures.FIXTURE_OPERATORS)
    assert reg.summary()["allowed"] == len(fixtures.FIXTURE_OPERATORS)
    info = reg.get("fx_drop_short", "1")
    assert info.kind == "filter" and info.input_columns == ("text",)
    assert reg.is_allowed("fx_drop_short", "1")
    assert reg.current_version("fx_drop_short") == "1"
    assert reg.current_version("ghost") is None
    with pytest.raises(OperatorError) as exc:
        reg.get("ghost", "1")
    assert exc.value.code == "operator_not_found"


def test_registry_refusals_are_002_operator_refusals() -> None:
    """002 catches OperatorRefusal from get(); our errors must be one."""
    reg = _build()
    with pytest.raises(operator_port.OperatorRefusal):
        reg.get("ghost", "1")


def test_duplicate_name_and_version_marks_both_and_neither_runs() -> None:
    """4.3."""
    a = _op_class(_manifest())
    b = _op_class(_manifest(description="another source"))
    reg = _build(a, b)
    rows = [(e, s) for e, s in reg.entries() if e.name == "dup_op"]
    assert [s for _, s in rows] == ["duplicate"]
    assert "more than one source" in (rows[0][0].error or "")
    with pytest.raises(OperatorError) as exc:
        reg.require_allowed("dup_op", "1")
    assert exc.value.code == "operator_invalid_manifest"
    assert not reg.is_allowed("dup_op", "1")


def test_unsupported_keyword_is_invalid_manifest_with_the_keyword_named() -> None:
    """4.4."""
    schema = {
        "type": "object",
        "properties": {"x": {"anyOf": [{"type": "string"}]}},
        "additionalProperties": False,
    }
    reg = _build(_op_class(_manifest("bad_schema", params_schema=schema)))
    entry, state = [(e, s) for e, s in reg.entries() if e.name == "bad_schema"][0]
    assert state == "invalid_manifest"
    assert "'anyOf' at /properties/x/anyOf (refused)" in (entry.error or "")
    assert not reg.is_allowed("bad_schema", "1")


def test_pydantic_invalid_manifest_dict_is_listed_not_raised(tmp_path: Path) -> None:
    catalogue = tmp_path / "c.json"
    catalogue.write_text(
        json.dumps({"operators": [{"manifest": {"name": "dj_x", "version": "1", "colour": 1}}]})
    )
    reg = OperatorRegistry.build(
        native=(), catalogues=((catalogue, "datajuicer"),), entry_points=()
    )
    entry, state = reg.entries()[0]
    assert state == "invalid_manifest" and entry.name == "dj_x"


def test_removed_version_names_the_current_one() -> None:
    """4.5 (T-11)."""
    reg = _build(_op_class(_manifest("moved", version="2")))
    with pytest.raises(OperatorError) as exc:
        reg.get("moved", "1")
    assert exc.value.code == "version_unavailable"
    assert exc.value.details["current_version"] == "2"


def test_thresholded_operator_without_statistics_is_invalid() -> None:
    """9.3: the registry refuses a thresholded native operator without compute_statistics."""
    schema = {
        "type": "object",
        "properties": {"m": {"type": "integer"}},
        "additionalProperties": False,
    }
    threshold = {"param": "m", "statistic": "len", "unit": "chars", "drop_when": "below"}
    without = _op_class(_manifest("thr_a", params_schema=schema, thresholds=[threshold]))
    with_stats = _op_class(
        _manifest("thr_b", params_schema=schema, thresholds=[threshold]), stats=True
    )
    states = {e.name: s for e, s in _build(without, with_stats).entries()}
    assert states == {"thr_a": "invalid_manifest", "thr_b": "allowed"}


def test_manifest_hash_comparison_refuses_another_manifest() -> None:
    """4.6."""
    reg = _build(*fixtures.FIXTURE_OPERATORS)
    entry = reg.entry("fx_drop_short", "1")
    check_manifest_hash(entry, manifest_hash(fixtures.DropShort.manifest))
    with pytest.raises(OperatorError) as exc:
        check_manifest_hash(entry, manifest_hash(fixtures.KeepAll.manifest))
    assert exc.value.code == "manifest_mismatch"


def test_class_that_fails_to_construct_is_failed_to_load() -> None:
    class Broken:
        def __init__(self) -> None:
            raise RuntimeError("boom")

    entry, state = _build(Broken).entries()[0]
    assert state == "failed_to_load" and "boom" in (entry.error or "")


def _calls(fn: Any) -> set[str]:
    tree = ast.parse(inspect.cleandoc(inspect.getsource(fn)))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def test_build_calls_every_loader() -> None:
    """4.7: build() CALLS each provider's loader (an AST check of the call, not a text search)."""
    called = _calls(OperatorRegistry.build)
    assert {
        "native_operators",
        "native_entries",
        "catalogue_entries",
        "list_entry_points",
        "_load_plugins",
    } <= called


def test_install_process_registry_installs_002s_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(operator_port, "_registry", operator_port.NoOperatorsInstalled())
    monkeypatch.setattr(registry_module, "_process", None)
    built = registry_module.current()
    assert operator_port.registry() is built
    assert built.is_allowed("fx_keep_all", "1")


def test_info_maps_output_column_roles_and_endpoint() -> None:
    reg = _build(*fixtures.FIXTURE_OPERATORS)
    info = reg.get("fx_endpoint_probe", "1")
    assert info.endpoint_role == "judge" and info.queue == "labeling"
    assert info.output_columns == {"judge_model": "metadata"}
    assert info.labels_rows
