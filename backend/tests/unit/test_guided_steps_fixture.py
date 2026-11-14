"""The frontend's ``META.guided_steps`` fixture is the backend's ``GUIDED_STEPS``.

``frontend/src/components/guided/guided.test.tsx`` derives "every step has a panel" from that
fixture, so the fixture must not drift from the authority (GET /datasets/meta serves
``GUIDED_STEPS``). The fixture line must be found exactly once: a layout change fails here loudly
instead of matching nothing.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from src.models.enums import GUIDED_STEPS

FIXTURE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "test" / "fixtures002.ts"
LINE = re.compile(r"^\s*guided_steps:\s*(\[[^\]]*\]),\s*$", re.M)


def test_the_frontend_fixture_lists_exactly_the_backend_steps() -> None:
    found = LINE.findall(FIXTURE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one guided_steps line in {FIXTURE.name}, found {found}"
    assert tuple(ast.literal_eval(found[0])) == GUIDED_STEPS
