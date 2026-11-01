"""Write ``backend/tests/fixtures/behaviours/names.json``: the behaviour-name guard's name source.

FR-016.33: names come from behaviour DATA only - every ``behaviour.json`` under
``backend/tests/fixtures/behaviours/*/`` (the fixture behaviours) and ``behaviours/*/`` (behaviour
packs) - never from a list in a test. Each declares ``behaviour_key`` and ``vocabulary``. The
loader fails closed: no source, a pack without a key, or a vocabulary term equal to a component of a
registered validator id (FTID 016 I9: format names are not behaviour names) is refused.
``test_behaviour_names_absent.py`` pins ``names.json`` to these bytes. Run from ``backend/``::

    python -m scripts.write_behaviour_names
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SOURCES = ("backend/tests/fixtures/behaviours/*/behaviour.json", "behaviours/*/behaviour.json")
NAMES_PATH = REPO / "backend" / "tests" / "fixtures" / "behaviours" / "names.json"


class NameSourceError(ValueError):
    pass


def collect(root: Path = REPO, validator_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Every declared behaviour, sorted by key; refuses what would make the guard lie."""
    if validator_ids is None:
        from src.rules.validators import VALIDATORS

        validator_ids = sorted({i for i, _ in VALIDATORS})
    components = {part for vid in validator_ids for part in re.split(r"[_\-]", vid) if part}
    found: list[dict[str, Any]] = []
    for pattern in SOURCES:
        for path in sorted(root.glob(pattern)):
            data = json.loads(path.read_text(encoding="utf-8"))
            key = data.get("behaviour_key")
            vocabulary = data.get("vocabulary")
            if not isinstance(key, str) or not key or not isinstance(vocabulary, list):
                raise NameSourceError(f"{path}: behaviour_key and vocabulary are required")
            for term in vocabulary:
                if str(term).lower() in components:
                    raise NameSourceError(
                        f"{path}: vocabulary term {term!r} equals a component of a registered "
                        "validator id; format names are not behaviour names (FTID 016 I9)"
                    )
            found.append(
                {
                    "behaviour_key": key,
                    "vocabulary": sorted(str(t) for t in vocabulary),
                    "source": path.relative_to(root).as_posix(),
                }
            )
    if not found:
        raise NameSourceError("no behaviour data found: the guard would check nothing")
    return sorted(found, key=lambda b: b["behaviour_key"])


def render(behaviours: list[dict[str, Any]]) -> bytes:
    names = sorted(
        {b["behaviour_key"] for b in behaviours} | {t for b in behaviours for t in b["vocabulary"]}
    )
    doc = {"behaviours": behaviours, "names": names, "name_count": len(names)}
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def main() -> None:
    NAMES_PATH.write_bytes(render(collect()))
    print(f"wrote {NAMES_PATH}")


if __name__ == "__main__":
    main()
