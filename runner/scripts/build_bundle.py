"""Build the runner's guest bundle directory (feature 018; FTDD §4.3, FTASKS 4.1; ENH-006 D41).

Two modes:

* **fetch** (the default; the runner image build and the ``runner-tests`` CI job): read the snapshot's
  URL and SHA-256 from the PACKAGED contract (``backend/src/schemas/data/midataworks-runner-v1.json``;
  no literal digest anywhere else), download it (or take ``--snapshot FILE``), refuse unless its
  bytes hash to the contract's value and the contract's ``bundle_digest`` recomputes from its own
  parts, then precompile it for this host (``snapshot.cwasm``; ``.cwasm`` is native code tied to the
  Wasmtime version and CPU, so it is never published) ::

      python runner/scripts/build_bundle.py --out "$RUNNER_BUNDLE_DIR"

* **from-parts** (the publisher, once per bundle): fetch every part by URL and SHA-256 from
  ``src.runner_contract.constants``, link ``libpython`` with the contract's shim into a reactor,
  pre-initialise it with ``wasmtime wizer`` and the contract's prelude, and print the snapshot's
  SHA-256 and size for ``GUEST_SNAPSHOT``. Snapshot bytes are NOT reproducible across builds (S0),
  which is why they are published once and every host fetches them ::

      python runner/scripts/build_bundle.py --from-parts --work /path/to/scratch --out DIR

Exit status 0 only when every digest matched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
_CONTRACT_IN_SRC = Path("src") / "schemas" / "data" / "midataworks-runner-v1.json"
#: The packaged contract where this script runs: the repository (``backend/src/...``, the
#: ``runner-tests`` job) or the runner image, which copies the script to ``/app/runner/scripts/``
#: and the contract to ``/app/src/...`` (runner/Dockerfile). Read from one of the two, never a
#: third copy; 018 Review R2 found the image build failing on the repository-only path.
PACKAGED_CONTRACT = next(
    (p for p in (BACKEND / _CONTRACT_IN_SRC, REPO / _CONTRACT_IN_SRC) if p.is_file()),
    BACKEND / _CONTRACT_IN_SRC,
)
FIXED_MTIME = 1_000_000_000


class BundleError(Exception):
    """A digest did not match, or a part could not be built: the bundle must not be used."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value: Any) -> bytes:
    """The contract's canonical JSON (the hash rule), restated: no backend import in fetch mode."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def bundle_digest(bundle: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of ``guest.bundle`` without its ``bundle_digest`` key."""
    return hashlib.sha256(
        canonical({k: v for k, v in bundle.items() if k != "bundle_digest"})
    ).hexdigest()


def load_contract(path: Path = PACKAGED_CONTRACT) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def check_contract_bundle(contract: dict[str, Any]) -> dict[str, Any]:
    """The bundle block, after its digest recomputes from its parts (a hand edit is refused)."""
    bundle: dict[str, Any] = contract["guest"]["bundle"]
    expected = bundle_digest(bundle)
    if bundle.get("bundle_digest") != expected:
        raise BundleError(
            f"contract bundle_digest {bundle.get('bundle_digest')!r} does not recompute ({expected})"
        )
    return bundle


def verify_file(path: Path, sha256: str, what: str) -> None:
    actual = sha256_file(path)
    if actual != sha256:
        raise BundleError(f"{what}: sha256 {actual} at {path}, contract says {sha256}")


def fetch(url: str, sha256: str, dest: Path) -> Path:
    """Download ``url`` to ``dest`` unless a file with the right digest is already there."""
    if dest.exists() and sha256_file(dest) == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=600) as response, tmp.open("wb") as out:
        shutil.copyfileobj(response, out)
    verify_file(tmp, sha256, url)
    tmp.replace(dest)
    return dest


def precompile(snapshot: Path, out: Path, max_wasm_stack: int) -> Path:
    """``snapshot.cwasm`` for THIS host's Wasmtime and CPU, with a sidecar naming what it came from."""
    import wasmtime

    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.max_wasm_stack = max_wasm_stack
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(snapshot))
    target = out / "snapshot.cwasm"
    target.write_bytes(module.serialize())
    (out / "snapshot.cwasm.json").write_text(
        json.dumps(
            {
                "snapshot_sha256": sha256_file(snapshot),
                "max_wasm_stack": max_wasm_stack,
                "wasmtime": _wasmtime_version(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return target


def _wasmtime_version() -> str:
    from importlib.metadata import version

    return version("wasmtime")


def run_fetch(out: Path, snapshot_file: Path | None, contract_path: Path, compile_: bool) -> str:
    contract = load_contract(contract_path)
    bundle = check_contract_bundle(contract)
    snap = bundle["snapshot"]
    out.mkdir(parents=True, exist_ok=True)
    target = out / "snapshot.wasm"
    if snapshot_file is not None:
        verify_file(snapshot_file, snap["sha256"], str(snapshot_file))
        if snapshot_file.resolve() != target.resolve():
            shutil.copyfile(snapshot_file, target)
    else:
        fetch(snap["url"], snap["sha256"], target)
    verify_file(target, snap["sha256"], "snapshot.wasm")
    if target.stat().st_size != snap["size"]:
        raise BundleError(
            f"snapshot.wasm is {target.stat().st_size} bytes, contract {snap['size']}"
        )
    if compile_:
        precompile(target, out, int(contract["limits_floor"]["wasm_stack_bytes"]))
    return str(snap["sha256"])


# --- from parts (the publisher) ------------------------------------------------------------------


def _extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)


def _extract_tar(archive: Path, dest: Path) -> None:
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")


def _touch_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        os.utime(path, (FIXED_MTIME, FIXED_MTIME), follow_symlinks=False)
    os.utime(root, (FIXED_MTIME, FIXED_MTIME))


def run_from_parts(work: Path, out: Path) -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND))
    from src.runner_contract import constants, prelude

    parts = constants.GUEST_BUNDLE_PARTS
    if prelude.prelude_sha256() != parts["prelude_sha256"]:
        raise BundleError("prelude digest disagrees with GUEST_BUNDLE_PARTS")
    work.mkdir(parents=True, exist_ok=True)
    dl = work / "downloads"
    interp = fetch(parts["interpreter"]["url"], parts["interpreter"]["sha256"], dl / "python.zip")
    libpy = fetch(parts["libpython"]["url"], parts["libpython"]["sha256"], dl / "build.zip")
    headers = fetch(parts["headers"]["url"], parts["headers"]["sha256"], dl / "cpython.tar.gz")
    sdk = fetch(parts["wasi_sdk"]["url"], parts["wasi_sdk"]["sha256"], dl / "wasi-sdk.tar.gz")
    cli = fetch(parts["preinit"]["url"], parts["preinit"]["sha256"], dl / "wasmtime.tar.xz")

    tree = work / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir()
    for archive, name, extract in (
        (libpy, "build", _extract_zip),
        (headers, "src", _extract_tar),
        (sdk, "sdk", _extract_tar),
        (cli, "cli", _extract_tar),
    ):
        extract(archive, tree / name)
    root = tree / "root"
    _extract_zip(interp, tree / "interp")
    (root / "lib").parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tree / "interp" / "lib"), str(root / "lib"))
    (root / "prelude").mkdir()
    (root / "prelude" / "prelude.py").write_text(prelude.PRELUDE_SOURCE, encoding="utf-8")
    _touch_tree(root)

    clang = next((tree / "sdk").glob("wasi-sdk-*/bin/clang"))
    include = next((tree / "src").glob("cpython-*/Include"))
    shim_c = tree / "shim.c"
    shim_c.write_text(prelude.SHIM_SOURCE, encoding="utf-8")
    build = tree / "build"
    archives = [str(build / a) for a in parts["libpython"]["archives"]]
    reactor = tree / "runner.wasm"
    subprocess.run(
        [
            str(clang),
            "--target=wasm32-wasip1",
            "-O2",
            "-D_WASI_EMULATED_SIGNAL",
            "-D_WASI_EMULATED_GETPID",
            "-D_WASI_EMULATED_PROCESS_CLOCKS",
            f"-I{include}",
            f"-I{build}",
            str(shim_c),
            *archives,
            *parts["wasi_sdk"]["link_flags"],
            "-o",
            str(reactor),
        ],
        check=True,
    )
    cli_bin = next((tree / "cli").glob("wasmtime-*/wasmtime"))
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / "snapshot.wasm"
    subprocess.run(
        [
            str(cli_bin),
            "wizer",
            "-S",
            "cli=y",
            "-f",
            parts["preinit"]["init_func"],
            "--dir",
            f"{root / 'lib'}::/lib",
            "--dir",
            f"{root / 'prelude'}::/prelude",
            "-o",
            str(snapshot),
            str(reactor),
        ],
        check=True,
    )
    return {
        "sha256": sha256_file(snapshot),
        "size": snapshot.stat().st_size,
        "path": str(snapshot),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, default=None, help="a local copy to verify")
    parser.add_argument("--contract", type=Path, default=PACKAGED_CONTRACT)
    parser.add_argument("--no-compile", action="store_true")
    parser.add_argument("--from-parts", action="store_true")
    parser.add_argument("--work", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.from_parts:
            if args.work is None:
                parser.error("--from-parts needs --work")
            print(json.dumps(run_from_parts(args.work, args.out), indent=2))
        else:
            digest = run_fetch(args.out, args.snapshot, args.contract, not args.no_compile)
            print(f"snapshot verified: {digest}")
    except BundleError as exc:
        print(f"BUNDLE REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
