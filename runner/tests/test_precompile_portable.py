"""``snapshot.cwasm`` is compiled for a fixed CPU level, not for the build host's CPU (2026-11-11).

The runner image is built on a CI runner and served on the cluster node. On 2026-11-11 the CI runner
had AVX-VNNI and the node did not: Wasmtime refused the precompiled module ("compilation setting
has_avx_vnni is enabled, but not available on the host"), the runner compiled the snapshot in
process (758 MB peak) and was OOM-killed under its 640Mi limit with an empty log.

These tests build the module the way the image build does (``build_bundle.precompile``) and read the
CPU features it records as enabled, from the same settings section Wasmtime checks at load time. The
set must be exactly the portable one. (Loading into an engine with the portable target is no check:
for the host's own triple Wasmtime compares against the host CPU, so a native build loads too.)
"""

from __future__ import annotations

import importlib.util
import json
import platform
import re
import struct
from pathlib import Path
from typing import Any

import pytest
import wasmtime

from src.runner import bundle as bundle_mod
from src.runner.host import Host

REPO = Path(__file__).resolve().parents[2]
PACKAGED = REPO / "backend" / "src" / "schemas" / "data" / "midataworks-runner-v1.json"

pytestmark = pytest.mark.skipif(
    platform.machine().lower() not in ("x86_64", "amd64"),
    reason="the portable CPU level is defined for x86-64 hosts only",
)


def _build_bundle() -> Any:
    spec = importlib.util.spec_from_file_location(
        "build_bundle_under_test", REPO / "runner" / "scripts" / "build_bundle.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stack() -> int:
    return int(json.loads(PACKAGED.read_text(encoding="utf-8"))["limits_floor"]["wasm_stack_bytes"])


@pytest.fixture(scope="module")
def precompiled(bundle_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("cwasm")
    snapshot = out / "snapshot.wasm"
    snapshot.write_bytes((bundle_dir / "snapshot.wasm").read_bytes())
    _build_bundle().precompile(snapshot, out, _stack())
    return out


def _engine_section(cwasm: bytes) -> bytes:
    """The ``.wasmtime.engine`` ELF section: the compilation settings Wasmtime checks against the
    host when it loads the module (the source of "compilation setting X is enabled, but not
    available on the host")."""
    shoff = struct.unpack_from("<Q", cwasm, 0x28)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", cwasm, 0x3A)
    headers = [
        struct.unpack_from("<IIQQQQIIQQ", cwasm, shoff + i * shentsize) for i in range(shnum)
    ]
    names_at = headers[shstrndx][4]
    for header in headers:
        start = names_at + header[0]
        if cwasm[start : cwasm.index(b"\0", start)] == b".wasmtime.engine":
            return cwasm[header[4] : header[4] + header[5]]
    raise AssertionError("no .wasmtime.engine section in snapshot.cwasm")


def _enabled_cpu_features(cwasm: bytes) -> set[str]:
    """CPU feature flags recorded as enabled: a length-prefixed ``has_*`` name, then the Bool tag
    (2) and true (1)."""
    section = _engine_section(cwasm)
    found = {
        m.group(2).decode() for m in re.finditer(rb"([\x04-\x20])(has_[a-z0-9_]+)\x02\x01", section)
    }
    assert found, "no CPU feature flags found: the settings layout changed; re-derive this reader"
    return found


def test_the_precompiled_module_carries_exactly_the_portable_feature_set(precompiled: Path) -> None:
    enabled = _enabled_cpu_features((precompiled / "snapshot.cwasm").read_bytes())
    assert enabled == set(_build_bundle().PORTABLE_X86_64_FLAGS), sorted(enabled)


def test_the_reader_sees_host_features_in_a_native_build(bundle_dir: Path) -> None:
    """Control for the test above: a module compiled for this host's own CPU is told apart by the
    same reader whenever the host has a feature beyond x86-64-v3 (every CI runner and the cluster
    node do: AVX-512). Skipped only on a host with nothing beyond the portable level."""
    native = wasmtime.Config()
    native.consume_fuel = True
    native.epoch_interruption = True
    native.max_wasm_stack = _stack()
    module = wasmtime.Module.from_file(wasmtime.Engine(native), str(bundle_dir / "snapshot.wasm"))
    extra = _enabled_cpu_features(module.serialize()) - set(_build_bundle().PORTABLE_X86_64_FLAGS)
    if not extra:
        pytest.skip("this host's CPU has nothing beyond x86-64-v3; the control cannot bite here")
    assert extra


def test_the_runner_loads_the_portable_module_without_falling_back(
    precompiled: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The runner's own engine takes the precompiled module: no in-process compile at start-up."""
    import logging

    with caplog.at_level(logging.WARNING, logger="midataworks.runner"):
        loaded = bundle_mod.load(
            Host.engine_for(json.loads(PACKAGED.read_text(encoding="utf-8"))),
            precompiled,
            PACKAGED,
        )
    assert loaded.status == "ok", loaded.detail
    assert not [r for r in caplog.records if "in process" in r.getMessage()], [
        r.getMessage() for r in caplog.records
    ]
