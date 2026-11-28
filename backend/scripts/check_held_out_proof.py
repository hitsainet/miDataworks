"""Recompute the held-out proofs of a published dataset version from its split files (017 FR-017.2).

A ``midataworks.dataset-version/v1`` manifest carries, on each value-directed held-out split,
``extensions.held_out_proof``: the disjointness proof the build stored. This script reads the
manifest and the downloaded split files, recomputes every proof with the same function the build
used (``held_out_files.prove_files``) and prints, per split, the recomputed record and whether it
equals the published one. Exit status 0 when every proof matches and passes, 1 otherwise. Run from
``backend/``::

    python -m scripts.check_held_out_proof <manifest.json> <directory holding the split paths>

Split rows a review excluded are left out of published files (``projection.omitted_excluded``); a
version with exclusions can then differ in its counts, which this script reports, never hides.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from src.services.curation.held_out_files import prove_files
from src.services.curation.held_out_proof import DirectedBy

KEY = "held_out_proof"
#: Keys a proof record gained after it was first published (FU-7a gap (j), 2026-11-27); a record
#: published before carries none of them and is compared without them.
ADDED_KEYS = frozenset({"training_rows", "compared_against_nothing", "note"})


def same_record(recomputed: dict[str, Any], published: dict[str, Any]) -> bool:
    """The recomputed record equals the published one, leaving out only the keys added since."""
    kept = {k: v for k, v in recomputed.items() if k in published or k not in ADDED_KEYS}
    return kept == published


def check(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    """``{"splits": {name: {"published", "recomputed", "matches"}}, "ok": bool}``."""
    splits = manifest["content"]["splits"]
    held = {s["name"] for s in splits if s.get("held_out")}
    training = {s["name"]: root / s["path"] for s in splits if s["name"] not in held}
    out: dict[str, Any] = {}
    for split in splits:
        published = (split.get("extensions") or {}).get(KEY)
        if not published:
            continue
        again = prove_files(
            split["name"], DirectedBy.from_proof(published), [root / split["path"]], training
        ).as_dict()
        out[split["name"]] = {
            "published": published,
            "recomputed": again,
            "matches": same_record(again, published),
            "passes": again["passes"],
        }
    return {"splits": out, "ok": all(v["matches"] and v["passes"] for v in out.values())}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    manifest = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    result = check(manifest, Path(args[1]))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
