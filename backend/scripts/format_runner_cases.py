"""Format ``docs/schemas/runner-cases-v1.json`` and refresh its packaged copy (FR-018.10; FTDD §4.5).

The cases file is HAND-WRITTEN; this script only fixes its bytes: sorted keys, two-space indent,
``ensure_ascii=True`` (every non-ASCII character and every lone surrogate a ``\\u`` escape, so an
editor cannot re-normalise a surrogate or a full-width digit in the vendored bytes) and exactly one
trailing newline. ``test_runner_cases_file.py`` fails when the docs file differs from
:func:`format_bytes`. ``--check`` only reports. Run from ``backend/``::

    python -m scripts.format_runner_cases [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.runner_contract.cases import CASES_FILE_NAME, PACKAGED_CASES_PATH

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / CASES_FILE_NAME


def format_bytes(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "ascii"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    content = format_bytes(json.loads(DOCS_PATH.read_text(encoding="utf-8")))
    if args.check:
        ok = DOCS_PATH.read_bytes() == content and PACKAGED_CASES_PATH.read_bytes() == content
        if not ok:
            print(
                "runner-cases-v1.json is not formatted; run: python -m scripts.format_runner_cases"
            )
        return 0 if ok else 1
    for path in (DOCS_PATH, PACKAGED_CASES_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
