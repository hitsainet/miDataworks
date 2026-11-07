"""Write ``docs/schemas/midataworks-rubric-v1.json`` and its packaged copy (FR-015.18).

The ONLY writer of both files (``test_rubric_schema_sync.py`` asserts no module in ``src/`` writes
them). miStudio's judge-prompt library vendors the docs copy byte-identical (miStudio 037 FR-15b).
Run from ``backend/``::

    python -m scripts.write_rubric_schema
"""

from __future__ import annotations

from pathlib import Path

from src.schemas.labeling import (
    RUBRIC_PACKAGED_SCHEMA_PATH,
    RUBRIC_SCHEMA_FILE_NAME,
    render_rubric_schema_file,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / RUBRIC_SCHEMA_FILE_NAME


def main() -> None:
    content = render_rubric_schema_file()
    for path in (SCHEMA_PATH, RUBRIC_PACKAGED_SCHEMA_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
