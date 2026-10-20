"""Every Hugging Face package the repo pins is pinned exactly, and to ONE version everywhere.

Operator directive 2026-10-18: Hugging Face software moves to the latest release in every place it
is declared, so the image, CI and the dev venv install the same versions. A pin bumped in one file
and forgotten in another is how a dev venv and an image end up on different majors (suite memory
``dev-venv-vs-image-version-drift``), so the pins are found by scanning every tracked requirements
and constraints file, not from a hand-kept list of files.

One exception, and it must keep earning its place: the Data Designer image stays on
huggingface-hub 1.x because data-designer requires ``huggingface-hub<2`` (its latest release,
0.9.5, still does). The exception holds only while that image pins data-designer.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

#: Hugging Face's own Python packages (normalised names).
HF_PACKAGES = {
    "accelerate",
    "bitsandbytes",
    "datasets",
    "evaluate",
    "hf-xet",
    "huggingface-hub",
    "optimum",
    "peft",
    "safetensors",
    "sentence-transformers",
    "tokenizers",
    "transformers",
    "trl",
}

#: (file, package) -> why it may differ from the rest of the repo.
EXCEPTIONS = {
    ("designer/requirements.txt", "huggingface-hub"): "data-designer requires huggingface-hub<2",
    ("designer/constraints.txt", "huggingface-hub"): "data-designer requires huggingface-hub<2",
}

REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*([<>=!~].*)?$")


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", *patterns],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def hf_pins() -> list[tuple[str, str, str]]:
    """(file, package, specifier) for every Hugging Face package in a requirements/constraints file."""
    pins: list[tuple[str, str, str]] = []
    for path in _tracked("*requirements*.txt", "**/*requirements*.txt", "**/constraints*.txt"):
        rel = str(path.relative_to(REPO))
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            m = REQ_LINE.match(line)
            assert m, f"{rel}: unparseable requirement {raw!r}"
            name = _norm(m.group(1))
            if name in HF_PACKAGES:
                pins.append((rel, name, (m.group(2) or "").replace(" ", "")))
    return pins


def test_the_scan_finds_the_backend_and_image_pins():
    # An empty scan would make every other test here vacuous.
    found = {(f, p) for f, p, _ in hf_pins()}
    for package in ("datasets", "huggingface-hub", "hf-xet"):
        assert ("backend/requirements.txt", package) in found, found
    assert ("datajuicer/constraints.txt", "huggingface-hub") in found, found


def test_every_hf_pin_is_exact():
    loose = [(f, p, s) for f, p, s in hf_pins() if not re.fullmatch(r"==[0-9][^,;]*", s)]
    assert not loose, f"Hugging Face packages must be pinned with ==: {loose}"


def test_every_hf_package_has_one_version():
    versions: dict[str, dict[str, set[str]]] = {}
    for f, p, s in hf_pins():
        if (f, p) in EXCEPTIONS:
            continue
        versions.setdefault(p, {}).setdefault(s, set()).add(f)
    split = {p: v for p, v in versions.items() if len(v) > 1}
    assert not split, f"Hugging Face pins disagree across files: {split}"


def test_the_designer_exception_is_still_needed():
    designer = (REPO / "designer" / "requirements.txt").read_text()
    assert re.search(r"^data-designer==", designer, re.MULTILINE), (
        "designer/ no longer pins data-designer; drop its huggingface-hub exception and move the "
        "image to the repo's huggingface-hub version"
    )
    for (f, p), _why in EXCEPTIONS.items():
        pinned = [s for pf, pp, s in hf_pins() if (pf, pp) == (f, p)]
        assert pinned and pinned[0].startswith("==1."), (f, p, pinned)
