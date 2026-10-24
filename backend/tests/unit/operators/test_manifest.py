"""Manifest, protocol and context (FR-003.1–003.4; FTASKS 2.1–2.4)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.core.canonical_json import canonical_sha256
from src.operators.context import OccurrenceAllocator, RunContext
from src.operators.errors import OperatorError
from src.operators.manifest import OperatorManifest, OperatorRef, ResourceSpec, manifest_hash
from src.operators.native.fixtures import DropShort
from src.operators.protocol import RowEvent

OPERATORS = Path(__file__).resolve().parents[3] / "src" / "operators"


def base(**over: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": "t_op",
        "version": "1",
        "provider": "native",
        "provider_version": "p1",
        "kind": "filter",
        "description": "A test operator.",
        "params_schema": {
            "type": "object",
            "properties": {"min_len": {"type": "integer"}, "label": {"type": "string"}},
            "additionalProperties": False,
        },
        "resources": {"queue": "curation"},
        "deterministic": True,
    }
    values.update(over)
    return values


REQUIRED = [
    "name",
    "version",
    "provider",
    "provider_version",
    "kind",
    "description",
    "params_schema",
    "resources",
    "deterministic",
]


@pytest.mark.parametrize("field", REQUIRED)
def test_each_field_is_required(field: str) -> None:
    values = base()
    del values[field]
    with pytest.raises(ValidationError):
        OperatorManifest(**values)


def test_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra"):
        OperatorManifest(**base(colour="blue"))


def test_manifest_is_frozen() -> None:
    manifest = OperatorManifest(**base())
    with pytest.raises(ValidationError):
        manifest.name = "other"  # type: ignore[misc]


def test_threshold_param_must_exist_and_be_numeric() -> None:
    spec = {"param": "ghost", "statistic": "len", "unit": "chars", "drop_when": "below"}
    with pytest.raises(ValidationError, match="not in params_schema"):
        OperatorManifest(**base(thresholds=[spec]))
    spec["param"] = "label"
    with pytest.raises(ValidationError, match="integer or number"):
        OperatorManifest(**base(thresholds=[spec]))
    spec["param"] = "min_len"
    assert OperatorManifest(**base(thresholds=[spec])).thresholds[0].param == "min_len"


def test_queue_and_provider_must_agree() -> None:
    with pytest.raises(ValidationError, match="datajuicer"):
        OperatorManifest(**base(resources={"queue": "datajuicer"}))
    with pytest.raises(ValidationError, match="datajuicer"):
        OperatorManifest(**base(provider="datajuicer"))
    ok = OperatorManifest(**base(provider="datajuicer", resources={"queue": "datajuicer"}))
    assert ok.resources.queue == "datajuicer"


def test_designer_queue_and_data_designer_provider_must_agree() -> None:
    """ADR-010 amendment: Data Designer runs only on the designer queue, and only it does."""
    with pytest.raises(ValidationError, match="designer"):
        OperatorManifest(**base(provider="data_designer"))
    with pytest.raises(ValidationError, match="designer"):
        OperatorManifest(**base(resources={"queue": "designer"}))
    ok = OperatorManifest(**base(provider="data_designer", resources={"queue": "designer"}))
    assert ok.resources.queue == "designer"


def test_name_and_provider_patterns() -> None:
    with pytest.raises(ValidationError):
        OperatorManifest(**base(name="Bad-Name"))
    with pytest.raises(ValidationError):
        OperatorManifest(**base(provider="pip:thing"))
    assert OperatorManifest(**base(provider="plugin:acme-ops")).provider == "plugin:acme-ops"


def test_lease_requires_an_endpoint_role() -> None:
    with pytest.raises(ValidationError, match="needs_lease"):
        OperatorManifest(**base(resources={"queue": "labeling", "needs_lease": True}))


def test_ref_parse_and_text() -> None:
    ref = OperatorRef.parse("fx_keep_all@1")
    assert (ref.name, ref.version, str(ref)) == ("fx_keep_all", "1", "fx_keep_all@1")
    with pytest.raises(ValueError):
        OperatorRef.parse("no-version")


# --- 2.2 the hash ------------------------------------------------------------------------------


def test_hash_is_canonical_json_sha256() -> None:
    manifest = OperatorManifest(**base())
    assert manifest_hash(manifest) == canonical_sha256(manifest.model_dump(mode="json"))


def test_golden_hash_of_a_fixture_manifest() -> None:
    """Pinned: a change to the manifest model or to the serialiser changes every recorded hash
    (and so every step identity, FR-002.28). Change this value only with that in mind."""
    manifest = OperatorManifest(**base())
    assert manifest_hash(manifest) == (
        "6ec3b01de242b8b3aee876ca12fd8e5429dd532fd957583b4f92900b544e2784"
    )


def test_hash_changes_with_any_field() -> None:
    a = OperatorManifest(**base())
    b = OperatorManifest(**base(description="Another."))
    assert manifest_hash(a) != manifest_hash(b)


def test_no_json_dumps_in_the_operator_package() -> None:
    """ADR-005: one canonical serialiser. ``json.dumps`` anywhere in operators/ is a second one."""
    offenders = []
    # The Data-Juicer and Data Designer runners live in other images and cannot import src (ADR-010); it writes
    # raw statistics and error.json, which nothing hashes. Named here, not pattern-matched.
    exempt = {OPERATORS / "datajuicer" / "runner.py", OPERATORS / "data_designer" / "runner.py"}
    for path in OPERATORS.rglob("*.py"):
        if path in exempt:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "dumps"
                and isinstance(node.value, ast.Name)
                and node.value.id == "json"
            ):
                offenders.append(f"{path.relative_to(OPERATORS)}:{node.lineno}")
    assert offenders == []


# --- 2.3 events ---------------------------------------------------------------------------------


def _event(**over: Any) -> RowEvent:
    values: dict[str, Any] = {
        "kind": "dropped",
        "row_key": "k" * 64,
        "occurrence": 0,
        "reason_code": "too_short",
        "reason": "short",
        "operator_name": "t",
        "operator_version": "1",
        "manifest_hash": "h",
        "statistic_name": "len",
    }
    values.update(over)
    return RowEvent(**values)


@pytest.mark.parametrize(
    "over",
    [{"reason_code": ""}, {"reason": "  "}, {"statistic_name": None}, {"kind": "vanished"}],
)
def test_event_without_reason_or_statistic_cannot_be_built(over: dict[str, Any]) -> None:
    with pytest.raises(OperatorError) as exc:
        _event(**over)
    assert exc.value.code == "event_invalid"


def test_changed_event_needs_new_key_and_added_needs_no_statistic() -> None:
    with pytest.raises(OperatorError):
        _event(kind="changed")
    assert _event(kind="added", statistic_name=None).kind == "added"
    with pytest.raises(OperatorError):
        _event(kind="split_assigned", statistic_name=None)


def test_event_file_row_matches_002_schema() -> None:
    from src.services.step_contract import EVENT_SCHEMA

    assert list(_event().file_row()) == EVENT_SCHEMA.names


# --- 2.4 the context -------------------------------------------------------------------------


def _ctx(seed: int = 7, **over: Any) -> RunContext:
    op = DropShort()
    values: dict[str, Any] = {
        "manifest": op.manifest,
        "manifest_hash": manifest_hash(op.manifest),
        "step_seed": seed,
        "job_id": None,
        "column_roles": {"text": "content", "note": "metadata"},
        "rowkey_scheme": "dw.rowkey/v1",
    }
    values.update(over)
    return RunContext(**values)


def test_same_seed_same_draws_different_seed_different_draws() -> None:
    a, b, c = _ctx(7), _ctx(7), _ctx(8)
    assert list(a.rng.integers(0, 10**9, 5)) == list(b.rng.integers(0, 10**9, 5))
    assert list(_ctx(7).rng.integers(0, 10**9, 5)) != list(c.rng.integers(0, 10**9, 5))


def test_endpoint_without_a_declared_role_raises() -> None:
    with pytest.raises(OperatorError) as exc:
        _ = _ctx().endpoint
    assert exc.value.code == "endpoint_role_missing"


def test_endpoint_role_unconfigured_names_the_settings_field() -> None:
    from src.operators.native.fixtures import EndpointProbe

    op = EndpointProbe()
    ctx = _ctx(manifest=op.manifest, manifest_hash=manifest_hash(op.manifest))
    with pytest.raises(OperatorError) as exc:
        _ = ctx.endpoint
    assert exc.value.code == "endpoint_unconfigured"
    assert exc.value.details["setting"] == "endpoint_roles.judge"


def test_helpers_fill_identity_and_allocate_fresh_occurrences() -> None:
    ctx = _ctx(allocator=OccurrenceAllocator([("a" * 64, 0), ("b" * 64, 0)]))
    dropped = ctx.drop({"_dw_row_key": "a" * 64, "_dw_occurrence": 0}, "r", "why", "len", 3, 5, "<")
    assert (dropped.operator_name, dropped.manifest_hash) == ("fx_drop_short", ctx.manifest_hash)
    assert dropped.threshold == '{"comparator":"<","value":5}'
    changed = ctx.change(("a" * 64, 0), "b" * 64, "c", "changed", "len")
    assert changed.new_occurrence == 1  # b@0 is taken by the input
    same = ctx.change(("a" * 64, 0), "a" * 64, "c", "meta only", "tag")
    assert same.new_occurrence == 0
    added = ctx.add("b" * 64, ["a" * 64], "gen", "generated")
    assert added.occurrence == 2 and added.parent_keys == ("a" * 64,)


def test_row_key_uses_content_columns_only() -> None:
    ctx = _ctx()
    assert ctx.row_key({"text": "x", "note": "1"}) == ctx.row_key({"text": "x", "note": "2"})
    assert ctx.content_columns == ["text"]


def test_resource_defaults() -> None:
    spec = ResourceSpec(queue="curation")
    assert (spec.endpoint_role, spec.needs_lease, spec.cpu_class) == (None, False, "light")
