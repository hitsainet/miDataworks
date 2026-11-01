"""Start-up verification of the guest bundle (FTDD §7.2; FR-018.4, FR-018.22).

Reads the packaged contract, recomputes its ``bundle_digest`` from the bundle block, hashes
``snapshot.wasm`` on disk against ``guest.bundle.snapshot.sha256`` and checks every import of the
module is in Table W. Any mismatch leaves the runner up with ``status = "bundle_mismatch"``: health
answers 503 and every call answers ``rule_worker_unavailable`` (it never runs an unverified guest).
A precompiled ``snapshot.cwasm`` is used only when its sidecar names this snapshot's digest, this
Wasmtime version and this stack size; otherwise the snapshot is compiled in process.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

import wasmtime

from src.runner_contract.hashing import bundle_digest

from . import wasi


@dataclass
class LoadedBundle:
    status: str  # "ok" | "bundle_mismatch"
    detail: str
    contract: dict[str, Any]
    module: wasmtime.Module | None
    snapshot_sha256: str | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_contract(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _module(engine: wasmtime.Engine, bundle_dir: Path, snapshot_sha: str, stack: int) -> Any:
    cwasm, sidecar = bundle_dir / "snapshot.cwasm", bundle_dir / "snapshot.cwasm.json"
    if cwasm.exists() and sidecar.exists():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except ValueError:
            meta = None
        if meta == {
            "snapshot_sha256": snapshot_sha,
            "max_wasm_stack": stack,
            "wasmtime": version("wasmtime"),
        }:
            try:
                return wasmtime.Module.deserialize_file(engine, str(cwasm))
            except wasmtime.WasmtimeError:
                pass  # built for another engine configuration: compile the verified snapshot
    return wasmtime.Module.from_file(engine, str(bundle_dir / "snapshot.wasm"))


def load(engine: wasmtime.Engine, bundle_dir: Path, contract_path: Path) -> LoadedBundle:
    contract = read_contract(contract_path)
    block = contract["guest"]["bundle"]
    if bundle_digest(block) != block.get("bundle_digest"):
        return LoadedBundle("bundle_mismatch", "contract bundle_digest", contract, None, None)
    snapshot = bundle_dir / "snapshot.wasm"
    if not snapshot.is_file():
        return LoadedBundle("bundle_mismatch", "snapshot.wasm absent", contract, None, None)
    actual = _sha256(snapshot)
    if actual != block["snapshot"]["sha256"]:
        return LoadedBundle("bundle_mismatch", "snapshot.wasm digest", contract, None, actual)
    stack = int(contract["limits_floor"]["wasm_stack_bytes"])
    module = _module(engine, bundle_dir, actual, stack)
    try:
        wasi.build_linker(engine, module)
    except KeyError:
        return LoadedBundle("bundle_mismatch", "import outside Table W", contract, None, actual)
    return LoadedBundle("ok", "", contract, module, actual)
