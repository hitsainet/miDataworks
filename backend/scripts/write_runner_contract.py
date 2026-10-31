"""Write ``docs/schemas/midataworks-runner-v1.json`` and its packaged copy (FR-018.1, FR-018.15).

The ONLY writer of both files (``test_runner_contract_sync.py`` asserts no module in ``src/`` writes
them). miStudio 040 and miLLM 039 vendor the docs copy byte-identical. It refuses while any S0 floor
value is unset. Run from ``backend/``::

    python -m scripts.write_runner_contract
"""

from __future__ import annotations

from pathlib import Path

from src.runner_contract.document import (
    CONTRACT_FILE_NAME,
    PACKAGED_CONTRACT_PATH,
    REGENERATE_COMMAND,
    render_contract_file,
)

DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "schemas" / CONTRACT_FILE_NAME


def main() -> None:
    content = render_contract_file()
    for path in (DOCS_PATH, PACKAGED_CONTRACT_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        print(f"wrote {path}")
    print(f"(regenerate with: {REGENERATE_COMMAND})")


if __name__ == "__main__":
    main()
