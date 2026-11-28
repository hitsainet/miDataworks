"""The paired-score source drops truncated rows when miWorkbench flags them (021 FTASKS 20.1 -
20.3; FR-021.33; suite P44, P66; R1B-X14; R2B-5).

The fixture's AUROCs are constants computed ONCE by an independent routine (sklearn's
``roc_auc_score`` over the hand-listed rows below, 2026-11-16), written here as literals, so the
fixture cannot agree with the filter by construction. Replace them with a recorded miWorkbench G5
answer (its origin commit noted) once that push exists.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any

import pytest

from src.services.detector_sets import mistudio_scores as ms
from src.services.detector_sets import paired

#: (score, label): the untruncated rows, then two truncated rows that move the AUROC
KEPT = [(0.9, 1), (0.8, 1), (0.7, 0), (0.35, 1), (0.3, 0), (0.2, 0), (0.1, 0), (0.6, 1)]
TRUNCATED = [(0.95, 0), (0.05, 1)]
#: sklearn.metrics.roc_auc_score over KEPT, and over KEPT + TRUNCATED (computed 2026-11-16)
AUROC_UNTRUNCATED = 0.875
AUROC_ALL = 0.56


def served(*, flags: list[bool | None] | None, **extra: Any) -> dict[str, Any]:
    rows = [
        {"position": i, "score": s, "label": label, "n_scored": 1}
        for i, (s, label) in enumerate(KEPT + TRUNCATED)
    ]
    if flags is not None:
        for row, flag in zip(rows, flags, strict=True):
            row["truncated"] = flag
    return {"rows": rows, "reported_auroc": AUROC_ALL, **extra}


FLAGS = [False] * len(KEPT) + [True] * len(TRUNCATED)


def accepted(selected: ms.Selected) -> paired.Acceptance:
    return paired.accept_source(
        "mistudio",
        [float(r["score"]) for r in selected.rows],
        [int(r["label"]) == 1 for r in selected.rows],
        reported_auroc=float(selected.reported_auroc or 0),
        reported_ci=None,
    )


def test_accept_source_drops_truncated() -> None:
    """With flags, the two truncated rows are dropped and the source is accepted against the
    untruncated AUROC (M-41: ignore the flag)."""
    got = ms.select_served_rows(served(flags=FLAGS, reported_auroc_untruncated=AUROC_UNTRUNCATED))
    assert isinstance(got, ms.Selected)
    assert len(got.rows) == len(KEPT) and got.excluded_truncated == 2
    assert got.reported_auroc == AUROC_UNTRUNCATED
    assert accepted(got).accepted
    # the same rows unfiltered against the same figure would be refused
    unfiltered = ms.Selected(served(flags=None)["rows"], AUROC_UNTRUNCATED, 0)
    assert not accepted(unfiltered).accepted


@pytest.mark.parametrize("flags", [None, [None] * 10], ids=["legacy_absent", "legacy_null"])
def test_truncated_legacy(flags: list[None] | None) -> None:
    got = ms.select_served_rows(served(flags=flags))
    assert isinstance(got, ms.Selected)
    assert len(got.rows) == 10 and got.reported_auroc == AUROC_ALL and got.excluded_truncated == 0
    assert accepted(got).accepted


def test_truncated_incomplete() -> None:
    flags: list[bool | None] = [*FLAGS[:-1], None]
    got = ms.select_served_rows(served(flags=flags, reported_auroc_untruncated=AUROC_UNTRUNCATED))
    assert isinstance(got, ms.Refusal) and got.reason.startswith("truncated_flag_incomplete")


def test_truncated_too_few_untruncated() -> None:
    """M-46: a null untruncated AUROC is miWorkbench's too_few_untruncated, not an absent key."""
    got = ms.select_served_rows(served(flags=FLAGS, reported_auroc_untruncated=None))
    assert isinstance(got, ms.Refusal) and got.reason.startswith("too_few_untruncated")
    assert "8 untruncated" in got.reason


@pytest.mark.parametrize(
    "reason", ["too_few_untruncated", "truncation_not_recorded", "evaluation_refused"]
)
def test_a_null_untruncated_auroc_is_refused_in_miworkbenchs_words(reason: str) -> None:
    """miWorkbench G5's ``served_untruncated_auroc`` serves a null figure for three reasons and
    names it in ``untruncated_reason``; the refusal states that reason, never relabels it."""
    got = ms.select_served_rows(
        served(flags=FLAGS, reported_auroc_untruncated=None, untruncated_reason=reason)
    )
    assert isinstance(got, ms.Refusal) and got.reason.startswith(f"{reason}: ")
    assert "8 untruncated" in got.reason


def test_untruncated_auroc_missing() -> None:
    got = ms.select_served_rows(served(flags=FLAGS))
    assert isinstance(got, ms.Refusal) and got.reason.startswith("untruncated_auroc_missing")


def test_join_calls_select_served_rows() -> None:
    """P66 (M-49): ``join`` calls ``select_served_rows(served)`` exactly once, and its result is the
    only source of the rows ``join_rows`` gets and of ``Joined.reported_auroc``."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(ms.join)))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "select_served_rows"
    ]
    assert len(calls) == 1
    assert [ast.unparse(a) for a in calls[0].args] == ["served"]
    subscripts = {
        ast.unparse(n.slice)
        for n in ast.walk(tree)
        if isinstance(n, ast.Subscript) and ast.unparse(n.value) == "served"
    }
    assert not {"'rows'", "'reported_auroc'", '"rows"', '"reported_auroc"'} & subscripts
