"""``drop_leakage_pairs@1.1.0``: several drop sides in one step (FU-7a gap (q), 2026-11-27).

The CHK1 repair needed two @1.0.0 steps (``fresh_id_test``, then ``fresh_unseen_generator``, each
against the same five kept sides). @1.1.0 takes ``drop_sides`` and must drop exactly the rows those
two steps drop, with the same reasons; @1.0.0 keeps its manifest (``expected.json`` pins its hash).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.storage import resolve_under_data_dir
from src.operators.errors import OperatorError
from src.operators.registry import OperatorRegistry
from src.services.identity import file_sha256
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

ROLES = {"text": "content"}
ARTEFACT = "runs/rep_test/reports/leakage_pairs.parquet"
KEEP = ["train", "id_test"]


def _rows() -> pa.Table:
    texts = [f"row number {i} of the fixture" for i in range(10)]
    return with_system_columns([{"text": t} for t in texts])


def _pair(kind: str, side_a: str, key_a: str, side_b: str, key_b: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "side_a": side_a,
        "side_b": side_b,
        "row_key_a": key_a,
        "row_key_b": key_b,
        "statistic": 1.0,
        "shared": "",
        "excerpt_a": "",
        "excerpt_b": "",
    }


def _pairs_file(keys: list[str]) -> str:
    """Pairs over three drop candidates: k0/k1 on fresh_a, k2/k3 on fresh_b, k4 on BOTH (one row
    key in two splits: the first side's reason must win), k5 between the two fresh sides only
    (never dropped: neither side is kept), k6 on a side that is not named."""
    pairs = [
        _pair("exact", "fresh_a", keys[0], "train", keys[9]),
        _pair("near", "train", keys[9], "fresh_a", keys[1]),
        _pair("near", "fresh_b", keys[2], "id_test", keys[8]),
        _pair("group", "fresh_b", keys[3], "train", keys[9]),
        _pair("near", "fresh_b", keys[4], "train", keys[9]),
        _pair("exact", "fresh_a", keys[4], "id_test", keys[8]),
        _pair("near", "fresh_a", keys[5], "fresh_b", keys[5]),
        _pair("near", "fresh_c", keys[6], "train", keys[9]),
    ]
    path = resolve_under_data_dir(ARTEFACT)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(pairs), path)
    return file_sha256(path)


def _params(sha: str, **sides: Any) -> dict[str, Any]:
    return {"pairs_artefact": ARTEFACT, "pairs_sha256": sha, "keep_sides": KEEP, **sides}


def _dropped(ran: Any) -> list[tuple[str, str]]:
    rows = ran.events.to_pylist()
    return [(r["row_key"], r["reason"]) for r in rows]


def test_one_step_drops_what_two_one_side_steps_drop(data_dir: Path) -> None:
    table = _rows()
    keys = [str(k) for k in table.column("_dw_row_key").to_pylist()]
    sha = _pairs_file(keys)
    first = run_operator(
        "drop_leakage_pairs", _params(sha, drop_side="fresh_a"), table, ROLES, version="1.0.0"
    )
    second = run_operator(
        "drop_leakage_pairs",
        _params(sha, drop_side="fresh_b"),
        first.output,
        ROLES,
        version="1.0.0",
    )
    both = run_operator(
        "drop_leakage_pairs",
        _params(sha, drop_sides=["fresh_a", "fresh_b"]),
        table,
        ROLES,
        version="1.1.0",
    )
    assert both.output.equals(second.output)
    assert sorted(_dropped(both)) == sorted(_dropped(first) + _dropped(second))
    gone = {k for k, _ in _dropped(both)}
    assert gone == {keys[0], keys[1], keys[2], keys[3], keys[4]}  # not k5 (fresh|fresh), not k6
    reason = dict(_dropped(both))[keys[4]]
    assert "id_test" in reason and "exact" in reason  # fresh_a's pair, the first side named


def test_the_new_version_still_takes_one_drop_side(data_dir: Path) -> None:
    table = _rows()
    keys = [str(k) for k in table.column("_dw_row_key").to_pylist()]
    sha = _pairs_file(keys)
    old = run_operator(
        "drop_leakage_pairs", _params(sha, drop_side="fresh_b"), table, ROLES, version="1.0.0"
    )
    new = run_operator(
        "drop_leakage_pairs", _params(sha, drop_side="fresh_b"), table, ROLES, version="1.1.0"
    )
    assert new.output.equals(old.output) and _dropped(new) == _dropped(old)


@pytest.mark.parametrize(
    "sides",
    [{}, {"drop_side": "fresh_a", "drop_sides": ["fresh_b"]}, {"drop_sides": ["train"]}],
    ids=["neither", "both", "kept"],
)
def test_an_ambiguous_or_kept_drop_side_is_refused(data_dir: Path, sides: dict) -> None:
    table = _rows()
    sha = _pairs_file([str(k) for k in table.column("_dw_row_key").to_pylist()])
    with pytest.raises(Exception) as caught:
        run_operator("drop_leakage_pairs", _params(sha, **sides), table, ROLES, version="1.1.0")
    assert "drop_side" in str(caught.value) or "kept whole" in str(caught.value)


def test_the_live_registry_runs_1_1_0_and_keeps_1_0_0() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    assert reg.current_version("drop_leakage_pairs") == "1.1.0"
    reg.require_allowed("drop_leakage_pairs", "1.0.0")
    sha = "0" * 64
    reg.require_valid_params(
        "drop_leakage_pairs", "1.1.0", _params(sha, drop_sides=["fresh_a", "fresh_b"])
    )
    with pytest.raises(OperatorError):  # @1.0.0 does not know the list
        reg.require_valid_params(
            "drop_leakage_pairs", "1.0.0", _params(sha, drop_sides=["fresh_a"])
        )
    with pytest.raises(OperatorError):  # an empty list is no side
        reg.require_valid_params("drop_leakage_pairs", "1.1.0", _params(sha, drop_sides=[]))
