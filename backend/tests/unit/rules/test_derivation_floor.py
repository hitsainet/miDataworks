"""A draft below the floor on held-out precision reads "low coverage", never "measured"."""

from __future__ import annotations

from src.rules import derivation
from tests.unit.rules.test_derivation import PROV, keyword_rows, params


def test_a_draft_below_the_floor_on_held_out_rows_is_low_coverage() -> None:
    texts, labels, groups = keyword_rows()
    # held-out negatives also carry the keyword: precision on held-out rows falls below the floor
    texts = [
        t + " zorbligate" if not y and derivation.held_out(g, 3, 0.3) else t
        for t, y, g in zip(texts, labels, groups, strict=True)
    ]
    out = derivation.sequential_cover(
        texts,
        labels,
        groups,
        params(),
        name="derived-test",
        behaviour_key="key-a",
        provenance=PROV,
        label_column="label",
    )
    assert out.result["literals"][0]["literal"] == "zorbligate"
    assert out.result["draft"]["precision"] is not None and out.result["draft"]["precision"] < 0.9
    assert out.state == "low_coverage" and out.document is not None
