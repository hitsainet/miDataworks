"""002 FTASKS 21.1 (suite tracker 4.9): 20.7's humor-shaped drop log re-run with the REAL operators:
005's ``threshold_labeler`` (the uncertainty band, from a published label run's probabilities),
004's ``cell_balancer`` (class balance) and 004's ``split`` (the held-out split), each through 003's
real executor (``execute_in_process``), chained as a build chains them.

The stub run (``test_humor_drop_log.py``) reproduced the prototype's 13,195 / 891 / 1,090; this one
says what the real operators do with the same shape (recorded 2026-10-16):

- the band: exactly 13,195 rows dropped, reason ``excluded_by_band`` (by construction);
- class balance: ``cell_balancer`` refuses the label column by design; 004's ``label_balancer``
  (added 2026-10-17, operator decision) drops exactly the prototype's 891, leaving 10,000 rows;
- the split: 004's stratified ``split`` at 0.1 holds out 1,000 of 10,000 (500 positive, 500
  negative), against the prototype's 1,090.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.operators.errors import StepFailed
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

IN_BAND, POSITIVE, NEGATIVE = 13_195, 5_000, 5_891
ROLES = {"text": "content", "note": "metadata"}
RUN = "lr_humor_real"


def _source() -> tuple[pa.Table, dict[str, float]]:
    rows: list[dict[str, object]] = []
    probability: list[float] = []
    for i in range(IN_BAND):
        rows.append({"text": f"uncertain headline number {i}", "note": None})
        probability.append(0.31 + 0.38 * (i % 100) / 100)  # strictly inside (0.3, 0.7)
    for i in range(POSITIVE):
        rows.append({"text": f"clearly funny headline number {i}", "note": None})
        probability.append(0.9)
    for i in range(NEGATIVE):
        rows.append({"text": f"clearly plain headline number {i}", "note": None})
        probability.append(0.1)
    table = with_system_columns(rows)
    keys = table.column("_dw_row_key").to_pylist()
    return table, dict(zip(keys, probability, strict=True))


def _publish_labels(data_dir: Path, probs: dict[str, float]) -> None:
    path = data_dir / "runs" / RUN / "labels.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"row_key": list(probs), "probability": list(probs.values())}), path)


def test_the_real_operators_reproduce_the_prototype_drop_log(data_dir: Path) -> None:
    source, probs = _source()
    _publish_labels(data_dir, probs)
    band = run_operator(
        "threshold_labeler",
        {
            "label_run_id": RUN,
            "threshold_positive": 0.7,
            "threshold_negative": 0.3,
            "drop_excluded": True,
        },
        source,
        ROLES,
    )
    assert len(band.events_by_reason("excluded_by_band")) == IN_BAND
    assert band.output.num_rows == POSITIVE + NEGATIVE
    try:
        run_operator("cell_balancer", {"column": "label"}, band.output, ROLES, seed=20261005)
    except StepFailed as exc:
        assert "is the label itself" in str(exc)
    else:
        raise AssertionError("cell_balancer balanced on the label; 004 says it must not")
    # 004's label_balancer (operator decision 2026-10-17) is the class balance: the prototype's 891
    balanced_reports: list[dict] = []
    balanced = run_operator(
        "label_balancer", {}, band.output, ROLES, seed=20261005, report_sink=balanced_reports
    )
    assert len(balanced.events_by_reason("label_cap")) == NEGATIVE - POSITIVE == 891
    assert balanced.output.num_rows == 2 * POSITIVE == 10_000
    assert balanced_reports[0]["leakage_warning"]["code"] == "label_balanced"
    split = run_operator(
        "split",
        {
            "split_names": ["train", "test"],
            "split_fractions": [0.9, 0.1],
            "held_out": ["test"],
            "stratify_by": ["label"],
        },
        balanced.output,
        ROLES,
        seed=20261005,
    )
    counts = Counter(split.output.column("_dw_split").to_pylist())
    test_labels = Counter(
        lab
        for lab, s in zip(
            split.output.column("label").to_pylist(),
            split.output.column("_dw_split").to_pylist(),
            strict=True,
        )
        if s == "test"
    )
    print(f"\nreal operators: band {IN_BAND}, split {dict(counts)}, test {dict(test_labels)}")
    assert counts == {"train": 9_000, "test": 1_000}, counts
    assert test_labels == {"positive": 500, "negative": 500}, test_labels
