"""Image-time check (T-10): running every offered operator installs nothing.

Run by datajuicer/Dockerfile during the build and by datajuicer/tests. Records the installed
distributions, runs each offered operator on the contract fixture through the runner (whose
``forbid_runtime_installs`` replaces Data-Juicer's installer with a refusal), and fails if any
operator raised or the installed set changed.

    python datajuicer/scripts/check_no_runtime_installs.py
"""

from __future__ import annotations

import json
import site
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dj_paths  # noqa: E402

from contract import outcome  # noqa: E402


def installed() -> list[str]:
    """Every distribution installed in the interpreter's site-packages directories, read from disk.

    Not ``importlib.metadata.distributions()``: that scans ``sys.path``, and importing ray adds
    its bundled third-party directory (which carries a colorama dist-info) to ``sys.path`` —
    a path change, not an install, which the first version of this check mistook for one.
    """
    found = []
    for directory in {*site.getsitepackages(), site.getusersitepackages()}:
        root = Path(directory)
        if root.is_dir():
            found.extend(
                f"{root.name}/{p.name}" for p in root.iterdir() if p.name.endswith(".dist-info")
            )
    return sorted(found)


def run() -> list[str]:
    """Problems found; empty means every offered operator ran with nothing installed."""
    cases = json.loads(dj_paths.CASES.read_text())
    before = installed()
    problems = []
    for op_name, params in sorted(cases.items()):
        try:
            outcome(op_name, params)
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            problems.append(f"{op_name}: {type(exc).__name__}: {exc}")
    after = installed()
    if after != before:
        added = sorted(set(after) - set(before))
        problems.append(f"the installed set changed while operators ran: {added}")
    return problems


def main() -> int:
    problems = run()
    for problem in problems:
        print(problem, file=sys.stderr)
    print("no runtime installs" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
