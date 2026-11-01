"""The README's local stack consumes every queue the backend routes work to.

Found 2026-10-17 (Foundation 18.1, clean clone): the README started workers for
``default,curation,labeling,publish,preview`` after ADR-006's 2026-10-16 amendment had moved
``import_source`` to its own ``imports`` queue, so a Hugging Face import from a stack built by
the README stayed ``queued`` forever, with nothing to say why. Queues served by their own images
(Data-Juicer, Data Designer; ADR-010) are not started from source and are excluded here.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core.celery_app import TASK_ROUTES, celery_app

README = Path(__file__).resolve().parents[3] / "README.md"
#: Served by their own images, never by a worker started from source (ADR-010).
OWN_IMAGES = {"datajuicer", "datajuicer_preview", "designer"}


def readme_workers() -> list[tuple[set[str], str]]:
    """Each ``celery ... worker`` command in the README: its queues and its full text."""
    text = README.read_text(encoding="utf-8").replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        if "celery" in line and " worker" in line:
            match = re.search(r"-Q\s+(\S+)", line)
            assert match, f"a README worker command names no queues: {line.strip()}"
            out.append(({q for q in match.group(1).split(",") if q}, line))
    return out


def routed_queues() -> set[str]:
    return {r["queue"] for r in TASK_ROUTES.values()} | {celery_app.conf.task_default_queue}


def test_every_routed_queue_has_a_readme_worker() -> None:
    served = set().union(*(queues for queues, _ in readme_workers()))
    missing = sorted(routed_queues() - OWN_IMAGES - served)
    assert not missing, f"README starts no worker for {missing}; work sent there waits forever"


def test_imports_run_one_at_a_time_as_in_production() -> None:
    """ADR-006 (2026-10-16): ``imports`` has its own worker at concurrency 1."""
    lines = [line for queues, line in readme_workers() if "imports" in queues]
    assert len(lines) == 1, lines
    queues = next(q for q, line in readme_workers() if "imports" in q)
    assert queues == {"imports"} and re.search(r"(-c\s*1|--concurrency[= ]1)\b", lines[0])


def test_the_guard_sees_the_queues_it_checks() -> None:
    """Self-test: the parse finds the README's commands and the routes name ``imports``."""
    assert len(readme_workers()) >= 2
    assert "imports" in routed_queues() and "default" in routed_queues()
