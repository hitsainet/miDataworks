"""Entry-point plugins and the two enforcement points (FR-003.10–003.12, 003.16; FTASKS 5.3–5.6)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

import pytest

from src.operators import executor, plugins
from src.operators.errors import OperatorError
from src.operators.native.fixtures import FIXTURE_OPERATORS
from src.operators.registry import OperatorRegistry
from src.services.operator_port import StepSpec
from tests.support import operator_fixtures as fx
from tests.support.plugin_dist import make_distribution

OPERATORS = Path(__file__).resolve().parents[3] / "src" / "operators"
TRIPLE = ("acme-ops", "1.0", "tagger")


class Allowed:
    """A fake allowlist reader: a mutable set, read on every check like the real one."""

    def __init__(self) -> None:
        self.triples: set[tuple[str, str, str]] = set()
        self.reads = 0

    def __call__(self) -> set[tuple[str, str, str]]:
        self.reads += 1
        return set(self.triples)


def _registry(allowed: Allowed) -> OperatorRegistry:
    return OperatorRegistry.build(native=FIXTURE_OPERATORS, catalogues=(), allowed_reader=allowed)


def test_listing_imports_nothing_allowing_then_loading_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5.3: list from metadata leaves no marker; allow + load writes it."""
    marker = make_distribution(tmp_path, monkeypatch)
    listed = [e for e in plugins.list_entry_points() if e.distribution == "acme-ops"]
    assert [e.triple for e in listed] == [TRIPLE]
    assert not marker.exists(), "listing an entry point must not import it"
    allowed = Allowed()
    reg = _registry(allowed)
    rows = {e.name: s for e, s in reg.entries() if e.provider.startswith("plugin:")}
    assert rows == {"tagger": "not_allowed"}
    assert not marker.exists()
    allowed.triples.add(TRIPLE)
    assert reg.is_allowed("acme_tagger", "1")  # lazy import at first use (5.6)
    assert marker.exists()
    assert "acme_ops" in sys.modules


def test_dot_load_is_called_only_in_load_allowed() -> None:
    """5.3 AST guard: ``.load(`` appears in exactly one function of the package."""
    sites = []
    for path in OPERATORS.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "load"
                    and not node.args
                ):
                    sites.append(f"{path.name}:{func.name}")
    assert sites == ["plugins.py:load_allowed"]


def test_failed_import_is_listed_and_the_rest_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5.4."""
    make_distribution(tmp_path / "a", monkeypatch, broken=True)
    allowed = Allowed()
    allowed.triples.add(TRIPLE)
    reg = _registry(allowed)
    states = {e.name: (s, e.error) for e, s in reg.entries()}
    assert states["tagger"][0] == "failed_to_load"
    assert "plugin exploded" in (states["tagger"][1] or "")
    assert states["fx_keep_all"][0] == "allowed"


def test_revoked_entry_point_is_refused_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """5.6: revoke takes effect on the next check (state read each time, never cached)."""
    make_distribution(tmp_path, monkeypatch)
    allowed = Allowed()
    allowed.triples.add(TRIPLE)
    reg = _registry(allowed)
    assert reg.is_allowed("acme_tagger", "1")
    allowed.triples.clear()
    with pytest.raises(OperatorError) as exc:
        reg.require_allowed("acme_tagger", "1")
    assert exc.value.code == "operator_not_allowed"
    assert reg.get("acme_tagger", "1").provider == "plugin:acme-ops"  # still known


def test_new_distribution_version_needs_a_new_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_distribution(tmp_path, monkeypatch, version="2.0")
    allowed = Allowed()
    allowed.triples.add(TRIPLE)  # version 1.0 was allowed, 2.0 is installed
    reg = _registry(allowed)
    rows = {e.name: s for e, s in reg.entries() if e.provider.startswith("plugin:")}
    assert rows == {"tagger": "not_allowed"}


def test_plugin_on_the_datajuicer_queue_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-003.16: datajuicer is reserved; such a manifest cannot even be built."""
    make_distribution(tmp_path, monkeypatch)
    source = (tmp_path / "acme_ops" / "__init__.py").read_text()
    (tmp_path / "acme_ops" / "__init__.py").write_text(
        source.replace('queue="curation"', 'queue="datajuicer"')
    )
    allowed = Allowed()
    allowed.triples.add(TRIPLE)
    states = {e.name: s for e, s in _registry(allowed).entries()}
    assert states["tagger"] == "failed_to_load"


# --- 5.5 two enforcement points ---------------------------------------------------------------


def _spec(reg: OperatorRegistry, data_dir: Path) -> StepSpec:
    fx.write_parts(data_dir / "in", fx.table())
    entry = reg.entry("acme_tagger", "1")
    return StepSpec(
        step_execution_id="00000000-0000-0000-0000-000000000001",
        operator="acme_tagger",
        version="1",
        params={},
        input_dir="in",
        output_dir="runs/j/steps/s",
        step_seed=1,
        job_id="j",
        bindings=[],
        column_roles=dict(fx.ROLES),
        rowkey_scheme="dw.rowkey/v1",
        expected_manifest_hash=str(entry.manifest_hash),
    )


@pytest.fixture
def plugin_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Allowed]:
    make_distribution(tmp_path / "plugin", monkeypatch)
    allowed = Allowed()
    allowed.triples.add(TRIPLE)
    reg = _registry(allowed)
    assert reg.is_allowed("acme_tagger", "1")  # import it while allowed
    return reg, allowed


def test_worker_refuses_when_the_api_check_is_removed(
    plugin_registry: tuple[Any, Allowed], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, allowed = plugin_registry
    spec = _spec(reg, data_dir)
    allowed.triples.clear()  # revoked after the recipe was planned
    sent: list[Any] = []
    monkeypatch.setattr(executor, "send_task", lambda name, **kw: sent.append((name, kw)))
    real = reg.require_allowed
    calls = {"n": 0}

    def api_check_removed(name: str, version: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # the API-side call in dispatch_step, removed
            return reg.entry(name, version)
        return real(name, version)

    monkeypatch.setattr(reg, "require_allowed", api_check_removed)
    executor.dispatch_step(reg, spec, "midataworks.versions.advance_build", ["j"])
    assert len(sent) == 1, "with the API check removed the step is queued"
    with pytest.raises(OperatorError) as exc:
        executor.execute_in_process(spec, registry=reg)
    assert exc.value.code == "operator_not_allowed"


def test_api_refuses_when_the_worker_check_is_removed(
    plugin_registry: tuple[Any, Allowed], data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg, allowed = plugin_registry
    spec = _spec(reg, data_dir)
    allowed.triples.clear()
    sent: list[Any] = []
    monkeypatch.setattr(executor, "send_task", lambda name, **kw: sent.append((name, kw)))
    with pytest.raises(OperatorError) as exc:
        executor.dispatch_step(reg, spec, "midataworks.versions.advance_build", ["j"])
    assert exc.value.code == "operator_not_allowed"
    assert sent == []


def test_allowed_plugin_runs_through_the_executor(
    plugin_registry: tuple[Any, Allowed], data_dir: Path
) -> None:
    reg, _ = plugin_registry
    spec = fx.stage_spec(reg, "acme_tagger", data=fx.table())
    result = executor.execute_in_process(spec, registry=reg)
    assert result.rows_kept == 8
