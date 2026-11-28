"""``next`` is unchanged by 022 (FR-022.8; FTASKS 6.3): five refusals' ``next``, captured at 022-P1
(``e68e38d``, before ``next_steps`` existed) through the envelope, still compare equal."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.errors import envelope
from tests.unit.test_refusal_next import triggered

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "next_golden_022.json"


def test_five_refusals_next_is_what_it_was() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    found = triggered()
    assert len(golden) == 5
    for code, expected in golden.items():
        details = envelope(code, "refused", found[code])["error"]["details"]
        assert details["next"] == expected, code
        assert details["next_steps"] == [expected], code
