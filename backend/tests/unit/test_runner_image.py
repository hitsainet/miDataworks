"""The runner image (feature 018; FTASKS 6.4, M29): what ``runner/Dockerfile`` builds and runs.

The image is built only in CI on the mirror (ADR-024), so nothing here builds it. The Dockerfile is
read for the properties that matter, and the bundle step it runs (``build_bundle.py`` fetch mode)
is RUN against a contract pointing at a local file: a snapshot whose bytes do not hash to the
contract's digest must stop the build (M29), and nothing in the Dockerfile may name a digest
itself (FTID E3 (a): the digest is read from the packaged contract, one place).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = (REPO / "runner" / "Dockerfile").read_text()
PACKAGED = REPO / "backend" / "src" / "schemas" / "data" / "midataworks-runner-v1.json"


def build_bundle() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_bundle_under_test", REPO / "runner" / "scripts" / "build_bundle.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instructions() -> list[str]:
    """The Dockerfile's instructions, continuation lines joined, comments dropped."""
    joined = re.sub(r"\s*\\\n\s*", " ", DOCKERFILE)
    return [
        line.strip() for line in joined.splitlines() if line.strip() and not line.startswith("#")
    ]


class TestTheDockerfile:
    def test_it_runs_as_the_non_root_uid_10001(self) -> None:
        users = [i.split()[1] for i in instructions() if i.startswith("USER ")]
        assert users and users[-1] == "runner"
        assert "useradd --create-home --uid 10001 runner" in DOCKERFILE
        assert instructions()[-1] == 'CMD ["python", "-m", "src.runner.server"]'

    def test_the_base_is_python_313_slim_without_a_gpu(self) -> None:
        bases = [i.split()[1] for i in instructions() if i.startswith("FROM ")]
        assert bases == ["python:3.13-slim"]
        assert "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NVIDIA_VISIBLE_DEVICES=void" in (
            instructions()
        )

    def test_it_installs_its_own_pins_held_to_its_constraints(self) -> None:
        installs = [i for i in instructions() if "pip install" in i]
        assert installs == [
            "RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/constraints.txt"
        ]

    def test_the_bundle_is_fetched_by_the_contracts_digest_and_nothing_else(self) -> None:
        """Fetch mode only: no --snapshot (a local file), --contract (another contract),
        --from-parts (a rebuild, not byte-reproducible) or --no-compile."""
        steps = [i for i in instructions() if "build_bundle.py" in i and i.startswith("RUN ")]
        assert steps == [
            'RUN python /app/runner/scripts/build_bundle.py --out "$RUNNER_BUNDLE_DIR" '
            '&& chmod -R a-w,a+rX "$RUNNER_BUNDLE_DIR"'
        ]

    def test_no_digest_is_written_in_the_dockerfile(self) -> None:
        assert not re.search(r"\b[0-9a-f]{64}\b", DOCKERFILE)
        assert "sha256" not in DOCKERFILE.lower().replace("# ", "")

    def test_the_bundle_stays_root_owned_and_read_only(self) -> None:
        """Review R1 L5: the native snapshot.cwasm must not be writable by the guest-running uid."""
        run_lines = [i for i in instructions() if i.startswith("RUN ")]
        assert not any("chown" in i for i in run_lines)
        assert not any(i.startswith(("COPY --chown", "ADD --chown")) for i in instructions())
        user_at = next(n for n, i in enumerate(instructions()) if i == "USER runner")
        bundle_at = next(n for n, i in enumerate(instructions()) if "build_bundle.py --out" in i)
        assert bundle_at < user_at, "the bundle is built (as root) before the image drops to runner"

    def test_it_copies_only_the_runner_and_the_contract_package(self) -> None:
        sources = set()
        for i in instructions():
            if i.startswith("COPY "):
                sources.update(t for t in i.split()[1:-1] if not t.startswith("--"))
        assert sources == {
            "runner/requirements.txt",
            "runner/constraints.txt",
            "backend/src/__init__.py",
            "backend/src/core/__init__.py",
            "backend/src/core/canonical_json.py",
            "backend/src/runner_contract/",
            "backend/src/runner/",
            "backend/src/schemas/__init__.py",
            "backend/src/schemas/data/midataworks-runner-v1.json",
            "runner/scripts/build_bundle.py",
        }


class TestTheBundleStep:
    """The step the Dockerfile runs, run here against a contract whose snapshot is a local file."""

    def contract_for(self, tmp_path: Path, payload: bytes, *, sha256: str, size: int) -> Path:
        contract = json.loads(PACKAGED.read_text(encoding="utf-8"))
        source = tmp_path / "published.wasm"
        source.write_bytes(payload)
        snap = contract["guest"]["bundle"]["snapshot"]
        snap.update(url=source.as_uri(), sha256=sha256, size=size)
        # The bundle digest covers the snapshot block; recompute it so only the bytes differ.
        bb = build_bundle()
        bundle = {k: v for k, v in contract["guest"]["bundle"].items() if k != "bundle_digest"}
        contract["guest"]["bundle"]["bundle_digest"] = bb.bundle_digest(bundle)
        path = tmp_path / "contract.json"
        path.write_text(json.dumps(contract), encoding="utf-8")
        return path

    def test_matching_bytes_are_accepted(self, tmp_path: Path) -> None:
        import hashlib

        payload = b"\x00asm-fixture-bytes"
        path = self.contract_for(
            tmp_path, payload, sha256=hashlib.sha256(payload).hexdigest(), size=len(payload)
        )
        out = tmp_path / "bundle"
        got = build_bundle().run_fetch(out, None, path, compile_=False)
        assert got == hashlib.sha256(payload).hexdigest()
        assert (out / "snapshot.wasm").read_bytes() == payload

    def test_bytes_that_do_not_hash_to_the_contract_stop_the_build_m29(
        self, tmp_path: Path
    ) -> None:
        import hashlib

        published = b"\x00asm-the-published-bytes"
        served = b"\x00asm-the-published-byteZ"  # one byte different, same size
        path = self.contract_for(
            tmp_path, served, sha256=hashlib.sha256(published).hexdigest(), size=len(published)
        )
        bb = build_bundle()
        with pytest.raises(bb.BundleError, match="sha256"):
            bb.run_fetch(tmp_path / "bundle", None, path, compile_=False)
        assert not (tmp_path / "bundle" / "snapshot.wasm").exists()


def test_the_bundle_step_finds_the_packaged_contract_where_the_image_puts_it(
    tmp_path: Path,
) -> None:
    """018 Review R2 (the image did not build): the Dockerfile's bundle step names no contract, so
    the script's DEFAULT must resolve to the contract the Dockerfile copies - in the image's own
    layout, derived from the COPY instructions, not the repository's."""
    import shutil

    destinations: dict[str, str] = {}
    for i in instructions():
        if i.startswith("COPY "):
            *sources, dest = i.split()[1:]
            for source in sources:
                target = dest + Path(source).name if dest.endswith("/") else dest
                destinations[source] = target
    script = destinations["runner/scripts/build_bundle.py"]
    contract = destinations["backend/src/schemas/data/midataworks-runner-v1.json"]
    root = tmp_path / "image"
    for source, target in ((REPO / "runner/scripts/build_bundle.py", script), (PACKAGED, contract)):
        placed = root / target.lstrip("/")
        placed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, placed)
    spec = importlib.util.spec_from_file_location("bb_in_image", root / script.lstrip("/"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PACKAGED_CONTRACT == root / contract.lstrip("/")
    assert (
        module.load_contract()["guest"]["bundle"]["bundle_digest"]
        == json.loads(PACKAGED.read_text(encoding="utf-8"))["guest"]["bundle"]["bundle_digest"]
    )
    # And in the repository, the backend's packaged copy (the runner-tests job).
    assert build_bundle().PACKAGED_CONTRACT == PACKAGED
