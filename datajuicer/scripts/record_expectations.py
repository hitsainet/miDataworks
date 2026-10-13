"""Record the Data-Juicer contract expectations (FTDD 003 section 10.3). Run deliberately, review
the diff: these files are what an engine upgrade is compared against.

    python datajuicer/scripts/record_expectations.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dj_paths  # noqa: E402

from contract import outcome  # noqa: E402


def main() -> int:
    cases = json.loads(dj_paths.CASES.read_text())
    dj_paths.EXPECTATIONS.mkdir(parents=True, exist_ok=True)
    for op_name, params in sorted(cases.items()):
        data = outcome(op_name, params)
        path = dj_paths.EXPECTATIONS / f"{op_name}.json"
        path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
        print(f"wrote {path}: kept {len(data['kept'])}, dropped {len(data['dropped'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
