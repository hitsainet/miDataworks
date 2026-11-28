"""No Celery signature is built without a pinned queue (2026-10-07).

A signature is almost always a ``link``/``link_error``, and a link is sent by the worker that ran
the parent task through THAT worker's Celery app. The Data-Juicer and Data Designer runners have
their own apps with their own default queues, so an unpinned callback to a backend task landed on
the runner's queue, where it is unregistered, and was discarded: every version build with a
Data-Juicer step hung at ``running`` in production. ``core.celery_app.linked_signature`` pins the
queue to the backend's route; this walks the AST and refuses any other way to build one.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED = {SRC / "core" / "celery_app.py"}


def _unpinned() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("signature", "s", "si")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in ("celery_app", "app", "current_app")
            ):
                found.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def test_every_signature_goes_through_linked_signature() -> None:
    assert not _unpinned(), (
        f"Celery signatures built without a pinned queue: {_unpinned()}. Use "
        "core.celery_app.linked_signature, or a link sent by a runner's app is discarded."
    )
