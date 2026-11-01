"""MinHash maths and ``dedup_minhash`` through 003's executor (FTASKS 9.1–9.3)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from src.services.curation import minhash as m
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

TEXT = {"text": "content"}


def test_estimate_tracks_exact_jaccard_on_small_sets() -> None:
    rng = np.random.default_rng(0)
    vocab = [f"w{i}" for i in range(60)]
    for _ in range(20):
        a = " ".join(rng.choice(vocab, size=30))
        b = " ".join(rng.choice(vocab, size=30))
        sa, sb = m.shingles(a, "word", 1), m.shingles(b, "word", 1)
        exact = m.exact_jaccard(set(sa.tolist()), set(sb.tolist()))
        sigs = m.signatures_for([a, b], kind="word", size=1, count=256, seed=3)
        assert abs(m.jaccard_estimate(sigs[0], sigs[1]) - exact) < 0.12


def test_identical_texts_estimate_one_and_short_texts_are_one_shingle() -> None:
    sigs = m.signatures_for(["a b", "a b"], kind="word", size=5, count=64, seed=1)
    assert m.jaccard_estimate(sigs[0], sigs[1]) == 1.0
    assert m.shingles("a b", "word", 5).size == 1


def test_band_layout_is_derived_from_the_threshold() -> None:
    low, high = m.band_layout(128, 0.3), m.band_layout(128, 0.9)
    assert low != high
    assert abs(high.midpoint - 0.9) < abs(low.midpoint - 0.9)
    assert low.bands * low.rows == high.bands * high.rows == 128


def test_permutations_are_seeded() -> None:
    a1, b1 = m.permutations(8, 5)
    a2, _ = m.permutations(8, 5)
    a3, _ = m.permutations(8, 6)
    assert a1.tolist() == a2.tolist() != a3.tolist() and b1.max() < 2**32


BASE = "the city council approved a new budget for road repairs on monday evening after debate"


def _table(texts: list[str], splits: list[str] | None = None) -> pa.Table:
    t = with_system_columns([{"text": x} for x in texts])
    if splits:
        t = t.set_column(t.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits))
    return t


def test_near_duplicates_dropped_with_kept_row_and_estimate(data_dir: Path) -> None:
    texts = [BASE, BASE + " today", "an entirely unrelated sentence about cooking pasta at home"]
    table = _table(texts)
    ran = run_operator("dedup_minhash", {"threshold": 0.6, "size": 3}, table, TEXT)
    dropped = ran.events_by_reason("near_duplicate_minhash")
    assert len(dropped) == 1
    keys = table.column("_dw_row_key").to_pylist()
    pair = sorted(keys[:2])
    assert dropped[0]["related_row_key"] == pair[0] and dropped[0]["row_key"] == pair[1]
    assert dropped[0]["statistic_name"] == "jaccard_estimate"
    assert dropped[0]["statistic_value"] >= 0.6
    assert '"value":0.6' in dropped[0]["threshold"]


def test_threshold_decides(data_dir: Path) -> None:
    texts = [BASE, BASE + " today"]
    assert (
        run_operator(
            "dedup_minhash", {"threshold": 0.99, "size": 3}, _table(texts), TEXT
        ).events.num_rows
        == 0
    )


def test_cross_split_groups_kept_and_reported(data_dir: Path) -> None:
    sink: list[dict] = []
    ran = run_operator(
        "dedup_minhash",
        {"threshold": 0.6, "size": 3},
        _table([BASE, BASE + " today"], ["train", "test"]),
        TEXT,
        report_sink=sink,
    )
    assert ran.events.num_rows == 0 and ran.output.num_rows == 2
    assert sink[0]["cross_split_group_count"] == 1 and sink[0]["basis"] == "lexical"


def test_statistics_give_each_row_its_best_estimate(data_dir: Path) -> None:
    from src.operators.native.curation.dedup_minhash import DedupMinhash
    from tests.support.curation_fixtures import registry

    reg = registry()
    entry = reg.entry("dedup_minhash", "1.0.0")
    assert entry.state == "allowed"
    impl = DedupMinhash()

    class Ctx:
        input_reader = None
        content_columns = ["text"]
        step_seed = 1

    stats = impl.compute_statistics(_table([BASE, BASE + " today", "zzz"]), {"size": 3}, Ctx())  # type: ignore[arg-type]
    values = stats["jaccard_estimate"].to_pylist()
    assert values[0] == values[1] > 0.5 and values[2] == 0.0


@pytest.mark.parametrize("threshold", [0.5, 0.8])
def test_layout_recorded(data_dir: Path, threshold: float) -> None:
    sink: list[dict] = []
    run_operator(
        "dedup_minhash", {"threshold": threshold}, _table(["a", "b"]), TEXT, report_sink=sink
    )
    layout = m.band_layout(128, threshold)
    assert sink[0]["band_layout"] == {"bands": layout.bands, "rows": layout.rows}
