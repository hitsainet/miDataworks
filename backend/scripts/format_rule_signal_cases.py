"""Format ``docs/schemas/rule-signal-cases-v1.json`` and refresh its packaged copy (FR-016.6, Q12 (a)).

The cases file is HAND-WRITTEN; this script only fixes its bytes: sorted keys, two-space indent,
``ensure_ascii=True`` (every non-ASCII character a ``\\u`` escape, so an editor that re-normalises
``•`` or ``５`` cannot change what consumers vendor) and exactly one trailing newline.
``test_rule_signal_cases_file.py`` fails when the file differs from :func:`format_bytes`. Run from
``backend/``::

    python -m scripts.format_rule_signal_cases
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.rules.cases import CASES_FILE_NAME, PACKAGED_CASES_PATH

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / CASES_FILE_NAME


def format_bytes(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def main() -> None:
    content = format_bytes(json.loads(DOCS_PATH.read_text(encoding="utf-8")))
    for path in (DOCS_PATH, PACKAGED_CASES_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
