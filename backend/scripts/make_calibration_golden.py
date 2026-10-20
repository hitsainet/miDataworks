"""Generate ``tests/fixtures/calibration/prototype_golden.json`` from the PROTOTYPE's own code
(006 FTASKS 3.11; FTID 006 section 8).

Builds three seeded synthetic frames, runs the prototype's ``validate_judge.metrics``,
``paired_probe.pairs_of``, ``paired_accuracy`` and ``cluster_ci`` on them, and writes inputs plus
outputs. The test suite reads only the committed JSON; this script is the only code that loads the
prototype, by file path, outside the suite.

    backend/.venv/bin/python backend/scripts/make_calibration_golden.py --prototype-dir scripts
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

OUT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "calibration"
    / "prototype_golden.json"
)


def load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame(seed: int, groups: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, Any]] = []
    for g in range(groups):
        pair = f"g{g:04d}"
        base = rng.random() * 0.3
        rows.append(
            {
                "id": f"{pair}-o",
                "pair_id": pair,
                "kind": "original",
                "split": "train",
                "meanGrade": np.nan,
                "human_label": None,
                "p_true": round(float(base), 6),
            }
        )
        for e in range(int(rng.integers(1, 4))):
            grade = float(np.round(rng.random() * 3, 1))
            label = 1 if grade >= 1.6 else (0 if grade <= 0.4 else None)
            p = float(np.clip(base + grade * 0.15 + rng.normal(0, 0.15), 0, 1))
            rows.append(
                {
                    "id": f"{pair}-e{e}",
                    "pair_id": pair,
                    "kind": "edited",
                    "split": "train",
                    "meanGrade": grade,
                    "human_label": label,
                    "p_true": round(p, 6),
                }
            )
    out = pd.DataFrame(rows)
    out["human_label"] = out["human_label"].astype("Int8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prototype-dir", required=True, type=Path)
    args = parser.parse_args()
    validate_judge = load(args.prototype_dir / "validate_judge.py")
    paired_probe = load(args.prototype_dir / "paired_probe.py")
    cases = []
    for seed, groups in ((11, 200), (12, 350), (13, 90)):
        f = frame(seed, groups)
        m = validate_judge.metrics(f)
        ends = f[(f.kind == "edited") & f.human_label.notna()].reset_index(drop=True)
        eval_frame = pd.DataFrame(
            {
                "pair_id": ends.pair_id,
                "label": np.where(ends.human_label.astype(int) == 1, "humorous", "not_humorous"),
            }
        )
        funny, plain, group = paired_probe.pairs_of(eval_frame)
        scores = ends.p_true.to_numpy()
        wins = paired_probe.paired_accuracy(scores, funny, plain)
        cases.append(
            {
                "seed": seed,
                "rows": json.loads(
                    f.astype(object).where(f.notna(), None).to_json(orient="records")
                ),
                "validate_judge": {
                    k: m[k]
                    for k in (
                        "auroc_confident_ends",
                        "auroc_ci95",
                        "paired_accuracy",
                        "reliability",
                        "p_bands",
                    )
                },
                "paired_probe": {
                    "pairs": sorted([int(a), int(b)] for a, b in zip(funny, plain, strict=True)),
                    "paired": round(float(wins.mean()), 4),
                    "paired_ci95": paired_probe.cluster_ci(
                        wins, group, np.random.default_rng(paired_probe.SEED)
                    ),
                },
            }
        )
    OUT.write_text(
        json.dumps(
            {"generator": "backend/scripts/make_calibration_golden.py", "cases": cases},
            separators=(",", ":"),
        )
        + "\n"
    )
    print(f"wrote {OUT} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
