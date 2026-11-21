"""Synthetic rows with the published 2026-10-05 counts (FTID 004 §8; FTASKS 3.1).

The prototype's ``dataset/`` directory is git-ignored, so the goldens are rebuilt from the
committed records. The counts below are COPIED from those files, with their source named, and
``test_humor_pool_matches_records`` re-reads the files so drift fails loudly.

Texts are synthetic and varied: each row's length and words come from a seeded draw that does NOT
depend on its label or source, so length cannot agree with the label by construction (the usual
reason a suite stays green over a shortcut bug).

- :func:`candidates` — the 10,914 training candidates (``records/labelling_run.json``
  ``train.by_source_and_label``), with ``source_label``, a derived ``format`` (joke-ish sources are
  ``joke``, the rest ``headline``, as ``scripts/build_balanced.py`` derives it), a unique ``id``, a
  label-derived ``label_probability`` and a ``text``.
- :func:`labelled_pool` — every non-excluded labelled row of both samples
  (``records/labelling_run.json`` ``crosstab_source_by_label`` without ``excluded``): 613 in the
  smallest format cell once Reddit ``news`` is excluded (``records/format_balanced.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from src.services.row_keys import ROWKEY_V1, compute_row_keys

RECORDS = Path(__file__).resolve().parents[3] / "records"

#: records/labelling_run.json -> train.by_source_and_label (10,914 rows).
CANDIDATE_COUNTS: dict[str, int] = {
    "joke|humorous": 3627,
    "joke|not_humorous": 357,
    "joke_clean|humorous": 602,
    "joke_clean|not_humorous": 31,
    "joke_dark|humorous": 179,
    "joke_dark|not_humorous": 184,
    "joke_dirty|humorous": 433,
    "joke_dirty|not_humorous": 70,
    "news|humorous": 3,
    "news|not_humorous": 1193,
    "news_headline|humorous": 613,
    "news_headline|not_humorous": 3622,
}

#: records/labelling_run.json -> crosstab_source_by_label, ``excluded`` left out.
POOL_COUNTS: dict[str, int] = {
    "joke|humorous": 3627,
    "joke|not_humorous": 415,
    "news_headline|humorous": 613,
    "news_headline|not_humorous": 4221,
    "joke_clean|humorous": 602,
    "joke_clean|not_humorous": 34,
    "joke_dark|humorous": 179,
    "joke_dark|not_humorous": 212,
    "joke_dirty|humorous": 433,
    "joke_dirty|not_humorous": 79,
    "news|humorous": 3,
    "news|not_humorous": 1387,
}

#: records/publish_prep.json -> humor-jev9b.test_cells.
PUBLISH_TEST_CELLS: dict[str, int] = {
    "joke|humorous": 363,
    "joke|not_humorous": 36,
    "joke_clean|humorous": 60,
    "joke_clean|not_humorous": 3,
    "joke_dark|humorous": 18,
    "joke_dark|not_humorous": 18,
    "joke_dirty|humorous": 43,
    "joke_dirty|not_humorous": 7,
    "news|not_humorous": 119,
    "news_headline|humorous": 61,
    "news_headline|not_humorous": 362,
}
PUBLISH_TRAIN, PUBLISH_TEST = 9824, 1090
SEED = 20261005
#: records/format_balanced.json
FORMAT_PER_CELL, FORMAT_TRAIN, FORMAT_TEST = 613, 2208, 244

WORDS = (
    "the a market cat rain tax budget pun bear river court vote moon bread chess storm "
    "doctor bank engine garden lamp quiet sudden late early red blue big small old new"
).split()

ROLES = {
    "text": "content",
    "source_label": "metadata",
    "format": "metadata",
    "id": "metadata",
    "label": "metadata",
    "label_probability": "metadata",
}


def format_of(source_label: str) -> str:
    return "joke" if source_label.startswith("joke") else "headline"


def _rows(counts: dict[str, int], seed: int, prefix: str) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    out: list[dict[str, Any]] = []
    serial = 0
    for cell, n in counts.items():
        source_label, label = cell.split("|")
        for _ in range(n):
            length = int(rng.integers(3, 25))  # independent of label and source
            words = rng.choice(WORDS, size=length)
            prob = float(rng.uniform(0.5, 1.0) if label == "humorous" else rng.uniform(0.0, 0.2))
            out.append(
                {
                    "text": f"{prefix}{serial} " + " ".join(words),
                    "source_label": source_label,
                    "format": format_of(source_label),
                    "id": f"{prefix}{serial:06d}",
                    "label": label,
                    "label_probability": round(prob, 4),
                }
            )
            serial += 1
    order = rng.permutation(len(out))
    return [out[i] for i in order]


def with_system_columns(
    rows: list[dict[str, Any]],
    *,
    split: str = "train",
    source_id: str = "src-humor",
    content: tuple[str, ...] = ("text",),
) -> pa.Table:
    """Rows plus real ``_dw_`` columns: row keys from feature 002's function over ``content``."""
    table = pa.Table.from_pylist(rows)
    keys = compute_row_keys(table, list(content), ROWKEY_V1)
    seen: dict[str, int] = {}
    occurrences = []
    for key in keys:
        occurrences.append(seen.get(key, 0))
        seen[key] = seen.get(key, 0) + 1
    n = table.num_rows
    return (
        table.append_column("_dw_row_key", pa.array(list(keys), pa.string()))
        .append_column("_dw_occurrence", pa.array(occurrences, pa.int32()))
        .append_column("_dw_split", pa.array([split] * n, pa.string()))
        .append_column("_dw_source_id", pa.array([source_id] * n, pa.string()))
        .append_column("_dw_origin", pa.array(["source"] * n, pa.string()))
    )


def candidates() -> pa.Table:
    return with_system_columns(_rows(CANDIDATE_COUNTS, SEED, "c"))


def labelled_pool() -> pa.Table:
    return with_system_columns(_rows(POOL_COUNTS, SEED + 1, "p"))


def read_records() -> dict[str, Any]:
    run = json.loads((RECORDS / "labelling_run.json").read_text(encoding="utf-8"))
    prep = json.loads((RECORDS / "publish_prep.json").read_text(encoding="utf-8"))
    balanced = json.loads((RECORDS / "format_balanced.json").read_text(encoding="utf-8"))
    return {"run": run, "prep": prep, "balanced": balanced}
