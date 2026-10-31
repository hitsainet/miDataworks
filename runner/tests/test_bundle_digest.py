"""The bundle on disk verifies against the contract (FR-018.4; FTASKS 4.1).

The bundle digest recomputes from the contract's own parts, and the snapshot on disk hashes to the
contract's value. A one-byte-different snapshot leaves the runner in ``bundle_mismatch`` (503, every
call ``rule_worker_unavailable``): it never runs an unverified guest.
"""

from __future__ import annotations

import json
from pathlib import Path

import wasmtime

from src.runner import bundle as bundle_mod
from src.runner.host import Host
from src.runner_contract.hashing import bundle_digest

PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "schemas"
    / "data"
    / "midataworks-runner-v1.json"
)


def test_the_contract_bundle_digest_recomputes() -> None:
    block = json.loads(PACKAGED.read_text(encoding="utf-8"))["guest"]["bundle"]
    assert block["bundle_digest"] == bundle_digest(block)


def _engine() -> wasmtime.Engine:
    """The runner's own engine (fuel, epoch, stack): a precompiled ``snapshot.cwasm`` only
    deserializes into an engine configured exactly like the one that built it."""
    return Host.engine_for(json.loads(PACKAGED.read_text(encoding="utf-8")))


def test_the_disk_bundle_loads_ok(bundle_dir: Path) -> None:
    engine = _engine()
    loaded = bundle_mod.load(engine, bundle_dir, PACKAGED)
    assert loaded.status == "ok", loaded.detail
    assert loaded.module is not None


def test_a_one_byte_different_snapshot_is_a_mismatch(bundle_dir: Path, tmp_path: Path) -> None:
    snapshot = (bundle_dir / "snapshot.wasm").read_bytes()
    fake = tmp_path
    (fake / "snapshot.wasm").write_bytes(snapshot[:-1] + bytes([snapshot[-1] ^ 0x01]))
    engine = _engine()
    loaded = bundle_mod.load(engine, fake, PACKAGED)
    assert loaded.status == "bundle_mismatch"
    assert loaded.module is None


def test_a_cwasm_built_for_another_engine_falls_back_to_the_verified_snapshot(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """Review R1: a ``snapshot.cwasm`` whose sidecar matches but which was compiled for a
    differently configured engine (here: no fuel, no epoch) must not crash the runner at start-up
    (``Module.deserialize_file`` raises); the runner compiles the verified ``snapshot.wasm``."""
    from importlib.metadata import version

    contract = json.loads(PACKAGED.read_text(encoding="utf-8"))
    stack = int(contract["limits_floor"]["wasm_stack_bytes"])
    snapshot = bundle_dir / "snapshot.wasm"
    (tmp_path / "snapshot.wasm").write_bytes(snapshot.read_bytes())
    plain = wasmtime.Config()
    plain.max_wasm_stack = stack
    other = wasmtime.Module.from_file(wasmtime.Engine(plain), str(snapshot))
    (tmp_path / "snapshot.cwasm").write_bytes(other.serialize())
    (tmp_path / "snapshot.cwasm.json").write_text(
        json.dumps(
            {
                "snapshot_sha256": contract["guest"]["bundle"]["snapshot"]["sha256"],
                "max_wasm_stack": stack,
                "wasmtime": version("wasmtime"),
            }
        ),
        encoding="utf-8",
    )
    loaded = bundle_mod.load(_engine(), tmp_path, PACKAGED)
    assert loaded.status == "ok", loaded.detail
    assert loaded.module is not None


def test_a_contract_whose_bundle_digest_does_not_recompute_is_a_mismatch(
    bundle_dir: Path, tmp_path: Path
) -> None:
    """Review R1: the start-up check of the contract's own ``bundle_digest`` (a hand-edited bundle
    block, e.g. a swapped snapshot sha256 with the old digest left in place) is load-bearing."""
    contract = json.loads(PACKAGED.read_text(encoding="utf-8"))
    contract["guest"]["bundle"]["bundle_digest"] = "0" * 64
    edited = tmp_path / "contract.json"
    edited.write_text(json.dumps(contract), encoding="utf-8")
    loaded = bundle_mod.load(_engine(), bundle_dir, edited)
    assert loaded.status == "bundle_mismatch" and loaded.module is None
    assert loaded.detail == "contract bundle_digest"
