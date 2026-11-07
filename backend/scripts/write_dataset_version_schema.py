"""Write ``docs/schemas/midataworks-dataset-version-v1.json`` and its packaged copy (FR-008.34).

The backend image is built from ``backend/`` and so cannot read ``docs/``; the worker validates
every manifest against ``src/schemas/data/<same name>``, a byte-identical copy written here in the
same call. The ONLY writer of both files (``test_dataset_version_schema_sync.py`` asserts no module in
``src/`` opens it for writing). Run from ``backend/``::

    python -m scripts.write_dataset_version_schema
"""

from __future__ import annotations

from pathlib import Path

from src.schemas.dataset_version import PACKAGED_SCHEMA_PATH, SCHEMA_FILE_NAME, render_schema_file

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / SCHEMA_FILE_NAME


def main() -> None:
    content = render_schema_file()
    for path in (SCHEMA_PATH, PACKAGED_SCHEMA_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
