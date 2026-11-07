"""The frontend image passes the manual address to the build (feature 013; ENH-004 §3.4).

``frontend/src/config/manual.ts`` reads ``VITE_MANUAL_BASE_URL`` when Vite builds, and Vite reads it
from the build's environment. Before 013 the Dockerfile declared no such argument, so an image could
never point Help at another copy of the manual. Images are built only in CI on the mirror, so this
reads the Dockerfile's INSTRUCTIONS (not its prose): in the build stage the argument is declared,
exported to the environment, and both come before ``npm run build``.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOCKERFILE = REPO / "frontend" / "Dockerfile"
MANUAL_TS = REPO / "frontend" / "src" / "config" / "manual.ts"
NAME = "VITE_MANUAL_BASE_URL"


def instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) per logical line: comments dropped, continuations joined."""
    out: list[tuple[str, str]] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line = pending + line
        pending = ""
        keyword, _, rest = line.partition(" ")
        out.append((keyword.upper(), rest.strip()))
    return out


def build_stage(steps: list[tuple[str, str]]) -> list[tuple[str, str]]:
    froms = [i for i, (k, _) in enumerate(steps) if k == "FROM"]
    assert len(froms) >= 2, "a build stage and a serving stage"
    return steps[froms[0] : froms[1]]


def test_the_build_stage_passes_the_manual_address_to_vite() -> None:
    stage = build_stage(instructions(DOCKERFILE.read_text()))
    index = {f"{k} {v}": i for i, (k, v) in enumerate(stage)}
    arg = next(i for i, (k, v) in enumerate(stage) if k == "ARG" and v.split("=")[0] == NAME)
    env = index[f"ENV {NAME}=${{{NAME}}}"]
    build = index["RUN npm run build"]
    assert arg < env < build


def test_the_default_stays_the_public_manual() -> None:
    """An empty argument falls back to DEFAULT_MANUAL_BASE_URL in manual.ts (pinned to the
    manual's own address by manualLinks.test.tsx)."""
    stage = build_stage(instructions(DOCKERFILE.read_text()))
    arg = next(v for k, v in stage if k == "ARG" and v.split("=")[0] == NAME)
    assert arg in (NAME, f"{NAME}=")
    assert f"import.meta.env.{NAME}" in MANUAL_TS.read_text()


def test_the_parser_sees_a_missing_argument() -> None:
    """Negative control: the same Dockerfile without the ARG line fails the first test's lookup."""
    text = "\n".join(
        line for line in DOCKERFILE.read_text().splitlines() if not line.startswith(f"ARG {NAME}")
    )
    stage = build_stage(instructions(text))
    assert not any(k == "ARG" and v.split("=")[0] == NAME for k, v in stage)
