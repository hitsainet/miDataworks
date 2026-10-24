"""Record ``tests/fixtures/rules/golden/expected.json`` (016 FTASKS 0.2).

Run ONLY on unchanged ``main`` (before a feature changes an existing operator), never to make a
failing golden test pass::

    cd backend && python -m tests.support.make_rule_goldens
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("OPERATOR_TEST_FIXTURES", "false")

from tests.support.rule_goldens import EXPECTED_PATH, record  # noqa: E402


def main() -> None:
    document = record()
    EXPECTED_PATH.write_text(
        json.dumps(document, indent=1, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {EXPECTED_PATH}")


if __name__ == "__main__":
    main()
