"""No WebAssembly binary is committed to the repository (FR-018.4; FTASKS 3.12).

The interpreter, the custom link and the snapshot are fetched or built at image time by digest; no
``.wasm`` or ``.cwasm`` ever enters the tree or its public mirror. Reads git's own tracked-file
list, so an untracked scratch file does not count.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.support.source_tree import REPO

WASM_MAGIC = b"\x00asm"
SIZE_BOUND = 4096  # a tracked file larger than this with the magic number is refused


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def test_no_tracked_file_is_a_wasm_binary() -> None:
    offenders = []
    for rel in tracked_files():
        path = REPO / rel
        if not path.is_file():
            continue
        if rel.endswith((".wasm", ".cwasm")):
            offenders.append(rel)
            continue
        try:
            head = path.read_bytes()[:8]
        except OSError:
            continue
        if head.startswith(WASM_MAGIC) and path.stat().st_size > SIZE_BOUND:
            offenders.append(rel)
    assert offenders == [], f"WebAssembly binaries must not be tracked: {offenders}"


def test_a_committed_wasm_fixture_is_red(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (tmp_path / "guest.wasm").write_bytes(WASM_MAGIC + b"\x01\x00\x00\x00" + b"x" * 5000)
    git("add", "-A")
    git("commit", "-qm", "wasm")
    out = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    offenders = [r for r in out if r.endswith(".wasm")]
    assert offenders == ["guest.wasm"]
