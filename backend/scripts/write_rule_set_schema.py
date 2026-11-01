"""Write ``docs/schemas/midataworks-rule-set-v1.json`` and its packaged copy (FR-016.10).

The ONLY writer of both files (``test_rule_set_schema_sync.py`` asserts no module in ``src/`` writes
them). miStudio 040 and miLLM 039 vendor the docs copy byte-identical. Run from ``backend/``::

    python -m scripts.write_rule_set_schema
"""

from __future__ import annotations

from pathlib import Path

from src.rules.rule_set import PACKAGED_SCHEMA_PATH, SCHEMA_FILE_NAME, render_schema_file

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / SCHEMA_FILE_NAME


def main() -> None:
    content = render_schema_file()
    for path in (SCHEMA_PATH, PACKAGED_SCHEMA_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
