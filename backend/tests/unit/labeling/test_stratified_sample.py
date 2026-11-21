"""``sample@1.1.0``'s stratified mode: the second-opinion selection (017 FTASKS 9.2; FR-017.38,
FR-017.15; FTID §3.8; TD-017.22).

Through 003's real executor over rows whose strata cross (genre x generator x kind x label), with
``label_conflict`` rows spread over every stratum and instructed rows (``placement = whole``).
Expected counts are computed here from the rows, not from the operator. Controls: M18 (the
conflict union removed), the registration order (1.1.0 current, 1.0.0 still served), and the
build-time resolver that fills the instructed rate from the runtime settings.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from typing import Any

import pyarrow as pa
import pytest

from src.core.config import get_settings
from src.operators.registry import OperatorRegistry
from src.services import step_params
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator

STRATA = ["genre", "generator", "kind", "label"]
ROLES = {
    "text": "content",
    "genre": "metadata",
    "generator": "metadata",
    "kind": "metadata",
    "label": "metadata",
    "label_conflict": "metadata",
    "placement": "metadata",
}


def rows() -> pa.Table:
    """3 genres x 2 generators x 2 kinds x 2 labels; stratum sizes 3..14 (some below the target),
    every 7th row a conflict (null on every 11th: not a conflict), 900 instructed rows."""
    out: list[dict[str, Any]] = []
    for n, (genre, generator, kind, label) in enumerate(
        itertools.product(
            ("email", "chat", "memo"), ("gen-a", "gen-b"), ("k1", "k2"), (True, False)
        )
    ):
        for i in range(3 + (n * 5) % 12):
            out.append(
                {
                    "text": f"{genre} {generator} {kind} {label} row {i}",
                    "genre": genre,
                    "generator": generator,
                    "kind": kind,
                    "label": label,
                    "label_conflict": None if i % 11 == 10 else (i % 7 == 0),
                    "placement": "middle",
                }
            )
    for i in range(900):
        out.append(
            {
                "text": f"instructed row {i}",
                "genre": "email",
                "generator": "gen-a",
                "kind": "k1",
                "label": bool(i % 2),
                "label_conflict": False,
                "placement": "whole",
            }
        )
    return hp.with_system_columns(out)


def params(**over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "mode": "stratified",
        "strata": STRATA,
        "per_stratum": 6,
        "always_keep_column": "label_conflict",
        "instructed_column": "placement",
        "instructed_values": ["whole"],
        "instructed_rate": 0.10,
        "instructed_min": 120,
    }
    out.update(over)
    return {k: v for k, v in out.items() if v is not None}


def stratum(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[c] for c in STRATA)


def test_each_stratum_meets_its_target_every_conflict_is_kept_and_instructed_rows_at_their_rate() -> (
    None
):
    table = rows()
    every = table.to_pylist()
    conflicts = {r["_dw_row_key"] for r in every if r["label_conflict"] is True}
    instructed = [r for r in every if r["placement"] == "whole"]
    pool = Counter(
        stratum(r) for r in every if r["placement"] != "whole" and r["label_conflict"] is not True
    )
    ran = run_operator("sample", params(), table, ROLES)
    kept = ran.output.to_pylist()
    keys = {r["_dw_row_key"] for r in kept}
    assert conflicts <= keys, "every conflict row is kept"
    counted = Counter(
        stratum(r) for r in kept if r["placement"] != "whole" and r["label_conflict"] is not True
    )
    for cell, available in pool.items():
        target = min(6, available)
        assert abs(counted[cell] - target) <= 1, (cell, counted[cell], target)
        assert counted[cell] == target  # exact when the rows are there
    kept_instructed = [r for r in kept if r["placement"] == "whole"]
    assert len(kept_instructed) == max(math.ceil(0.10 * len(instructed)), 120)
    assert len(kept) == len(conflicts) + sum(min(6, n) for n in pool.values()) + 120
    (report,) = ran.meta["reports"]
    assert report["always_kept"] == len(conflicts)
    assert report["instructed"]["kept"] == 120 and report["instructed"]["available"] == 900
    assert report["strata_short"] == sorted(
        " | ".join(f"{c}={v}" for c, v in zip(STRATA, cell, strict=True))
        for cell, n in pool.items()
        if n < 6
    )
    # every row left out says why, with its stratum and rank
    dropped = ran.events_by_reason("not_sampled")
    assert len(dropped) == table.num_rows - len(kept)
    assert all(e["statistic_name"] == "sample_rank" for e in dropped)


def test_the_rate_wins_over_the_minimum_when_it_asks_for_more() -> None:
    ran = run_operator("sample", params(instructed_rate=0.5, instructed_min=10), rows(), ROLES)
    assert sum(1 for r in ran.output.to_pylist() if r["placement"] == "whole") == 450


def test_the_draw_is_seeded_and_independent_of_row_order() -> None:
    table = rows()
    a = run_operator("sample", params(seed=3), table, ROLES)
    b = run_operator(
        "sample", params(seed=3), table.take(list(reversed(range(table.num_rows)))), ROLES
    )
    c = run_operator("sample", params(seed=4), table, ROLES)
    keys = lambda r: sorted(r.output.column("_dw_row_key").to_pylist())  # noqa: E731
    assert keys(a) == keys(b) and keys(a) != keys(c)


def test_count_and_fraction_modes_are_unchanged_in_1_1_0() -> None:
    table = rows()
    for p in ({"mode": "count", "count": 40, "seed": 5}, {"mode": "fraction", "fraction": 0.2}):
        old = run_operator("sample", p, table, ROLES, version="1.0.0")
        new = run_operator("sample", p, table, ROLES, version="1.1.0")
        assert (
            new.output.column("_dw_row_key").to_pylist()
            == old.output.column("_dw_row_key").to_pylist()
        )


def test_1_1_0_is_current_and_1_0_0_is_still_served() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    assert reg.current_version("sample") == "1.1.0"
    assert reg.entry("sample", "1.0.0").manifest is not None
    assert reg.entry("sample", "1.1.0").manifest is not None


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        ({"per_stratum": None}, "needs per_stratum"),
        ({"strata": None}, "needs strata"),
        ({"count": 5}, "remove count"),
        ({"instructed_values": None}, "together"),
    ],
)
def test_stratified_parameters_are_checked_at_validation(
    bad: dict[str, Any], fragment: str
) -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    problems = reg.validate_params("sample", "1.1.0", params(**bad))
    assert any(fragment in p for p in problems), problems
    assert any(
        "stratified mode only" in p
        for p in reg.validate_params(
            "sample", "1.1.0", {"mode": "count", "count": 3, "strata": ["genre"]}
        )
    )


def test_the_build_fills_the_instructed_rate_from_the_runtime_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step's identity records the rate in force: SECOND_OPINION_INSTRUCTED_RATE / _MIN."""
    step = {"operator": "sample", "params": params(instructed_rate=None, instructed_min=None)}
    filled = step_params.resolve_step(step, lambda n, v: None)["params"]
    settings = get_settings()
    assert filled["instructed_rate"] == settings.second_opinion_instructed_rate == 0.10
    assert filled["instructed_min"] == settings.second_opinion_instructed_min == 500
    monkeypatch.setattr(settings, "second_opinion_instructed_rate", 0.25)
    monkeypatch.setattr(settings, "second_opinion_instructed_min", 7)
    again = step_params.resolve_step(step, lambda n, v: None)["params"]
    assert (again["instructed_rate"], again["instructed_min"]) == (0.25, 7)
    # a step giving its own values, or not stratified, is untouched
    own = {"operator": "sample", "params": params()}
    assert step_params.resolve_step(own, lambda n, v: None) == own
    plain = {"operator": "sample", "params": {"mode": "count", "count": 3}}
    assert step_params.resolve_step(plain, lambda n, v: None) == plain


def test_a_step_whose_rate_was_never_filled_is_refused_in_the_worker() -> None:
    from src.operators.errors import StepFailed

    with pytest.raises(StepFailed) as refused:
        run_operator("sample", params(instructed_rate=None), rows(), ROLES)
    assert refused.value.code == "params_invalid"
