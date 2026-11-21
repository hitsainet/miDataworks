"""``services/wsp_partitions.py`` (021 FTID I5, I7) and the character unit it measures in."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pytest


def test_character_count_is_code_points_not_grapheme_clusters() -> None:
    """021 FTASKS 0.7 (R1A-D21): ``pc.utf8_length`` counts Unicode code points, Python's ``len``.

    An emoji with a skin-tone modifier is ONE grapheme cluster and TWO code points; a decomposed
    "e" + combining acute is two code points. The WSP ``length`` and ``min_chars`` promise
    "characters" in this unit (docs/versions.md)."""
    values = ["abc", "é", "é", "\U0001f44d\U0001f3fd", "日本語", ""]
    counted = pc.utf8_length(pa.array(values)).to_pylist()
    assert counted == [3, 1, 2, 2, 3, 0]
    assert counted == [len(v) for v in values]
    assert pc.binary_length(pa.array(values)).to_pylist() != counted  # not bytes either


def _write(path: Path, texts: list[str | None], keys: list[str] | None = None) -> Path:
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    keys = keys or [f"{i:064x}" for i in range(len(texts))]
    pq.write_table(
        pa.table(
            {
                "_dw_row_key": pa.array(keys, pa.string()),
                "_dw_origin": pa.array(["source"] * len(texts), pa.string()),
                "text": pa.array(texts, pa.string()),
            }
        ),
        path,
    )
    return path


def test_measure_counts_code_points_with_the_lower_median(data_dir: Path) -> None:
    from src.services.wsp_partitions import measure

    path = _write(data_dir / "m.parquet", ["ab", "abcd", None, "\U0001f44d\U0001f3fd"])
    length, _ = measure(path, "text")
    assert length == {"unit": "characters", "min": 0, "max": 4, "median": 2}


def test_an_empty_written_wsp_split_is_refused(data_dir: Path) -> None:
    """FR-021.9: a marked split WRITTEN with no rows (a declared-empty held-out split of a
    behaviour plan) is refused like one the build never wrote (M-15)."""
    from src.services.assembly import BuildRefusal
    from src.services.wsp_partitions import check_finalize

    empty = _write(data_dir / "staging" / "hold.parquet", [])
    roles = {
        "hold": {
            "held_out": True,
            "wsp": {
                "role": "wsp_heldout",
                "set_kind": "everyday_text",
                "text_column": "text",
                "min_chars": 0,
            },
        }
    }
    with pytest.raises(BuildRefusal) as caught:
        check_finalize(None, [("hold", empty)], roles, [], roles_declared_by_step=True)
    assert caught.value.code == "wsp_partition_empty"
    assert caught.value.details["split"] == "hold"
