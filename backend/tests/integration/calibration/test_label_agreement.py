"""A calibration set's human labels against a training label (ENH-006 D116 G4; FTASKS 20.14).

Reproduces the dry run's hand read on rows of the same shape: true rows 76 of 95, false rows without
masked 86 of 96, over 231 of the set's 300 labels (ENH-006 D114, D115). The label column is
``deployed_truth``, and a decoy ``label`` column that disagrees sits beside it: every fixture's label
column being the run's own is how the dry run's worst fault stayed green.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import httpx
import pytest
from greenlet import getcurrent
from mcp.server.mcpserver.exceptions import ToolError

from src.services.calibration import label_agreement
from tests.support.gap_fixtures import calibration_set, row_key, split_version
from tests.support.mcp_live import call, live_mcp, refusal_of

YES, NO = "presented", "not_presented"


def wilson_by_quadratic(agree: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """The interval from its defining quadratic, not the production function."""
    p = agree / n
    a = 1 + z * z / n
    b = -(2 * p + z * z / n)
    c = p * p
    root = math.sqrt(b * b - 4 * a * c)
    return (-b - root) / (2 * a), (-b + root) / (2 * a)


def reference() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str | None]]:
    """The dry run's shape, in the order a build leaves it: nothing lines up with the answers."""
    rows: list[dict[str, Any]] = []
    answers: dict[str, str | None] = {}

    def add(n: int, truth: bool, kind: str, framing: str, said_yes: int, tag: str) -> None:
        for i in range(n):
            text = f"{tag} row {i}"
            rows.append(
                {
                    "text": text,
                    "deployed_truth": truth,
                    "label": "negative" if truth else "positive",  # the decoy
                    "kind": kind,
                    "framing": framing if i % 2 else "other_framing",
                }
            )
            answers[row_key(text)] = YES if i < said_yes else NO

    add(95, True, "pos", "named_person", 76, "true")
    add(96, False, "other_number", "discussed", 10, "false")
    add(40, False, "masked", "named_person", 40, "masked")
    for i in range(50):  # rows the set never saw
        rows.append(
            {
                "text": f"never asked {i}",
                "deployed_truth": bool(i % 2),
                "label": "positive",
                "kind": "pos",
                "framing": "named_person",
            }
        )
    for i in range(69):  # labelled, and gone from this version: its filter removed them
        answers[row_key(f"filtered out {i}")] = YES if i % 3 else NO
    return {"train": rows[::2], "test": rows[1::2]}, answers


async def _read(arguments: dict[str, Any]) -> dict[str, Any]:
    mcp, mcp_client = live_mcp()
    try:
        answer: dict[str, Any] = await call(
            mcp, "dataworks_get_calibration_label_agreement", arguments
        )
        return answer
    finally:
        await mcp_client.close()


def _arguments(set_id: str, version: str, **extra: Any) -> dict[str, Any]:
    return {
        "calibration_set_id": set_id,
        "version_id": version,
        "label_column": "deployed_truth",
        "positive_value": "true",
        **extra,
    }


@pytest.fixture
def made(data_dir: Path, clean_db: None) -> tuple[str, str]:
    splits, answers = reference()
    version = split_version(data_dir, splits)
    # The set was made on another version: its answers follow rows through rebuilds by row key.
    other = split_version(data_dir, {"train": [{"text": "the set's own version"}]})
    return calibration_set(other, answers, label_set=(YES, NO)), version


async def test_it_reproduces_the_dry_runs_figures_leaving_masked_out(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    set_id, version = made
    out = await _read(_arguments(set_id, version, exclude={"kind": ["masked"]}))
    assert out["labelled_rows"] == 300
    assert out["labelled_rows_found"] == 231 and out["labelled_rows_not_found"] == 69
    assert out["rows_excluded"] == 40 and out["rows_read"] == 231
    assert out["human_positive"] == YES
    true_rows, false_rows = out["overall"]["column_yes"], out["overall"]["column_no"]
    assert (true_rows["agree"], true_rows["rows"]) == (76, 95)
    assert (false_rows["agree"], false_rows["rows"]) == (86, 96)
    assert (out["overall"]["agree"], out["overall"]["rows"]) == (162, 191)
    for figure, agree, n in ((true_rows, 76, 95), (false_rows, 86, 96), (out["overall"], 162, 191)):
        low, high = wilson_by_quadratic(agree, n)
        assert figure["agreement"] == pytest.approx(agree / n)
        assert figure["ci_low"] == pytest.approx(low)
        assert figure["ci_high"] == pytest.approx(high)
    # the record's own words: interval 82% to 94%, and 71% to 87%
    assert round(false_rows["ci_low"], 2) == 0.82 and round(false_rows["ci_high"], 2) == 0.94
    assert round(true_rows["ci_low"], 2) == 0.71 and round(true_rows["ci_high"], 2) == 0.87


async def test_masked_left_in_changes_only_the_false_rows(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    set_id, version = made
    out = await _read(_arguments(set_id, version))
    assert out["rows_excluded"] == 0
    assert (out["overall"]["column_yes"]["agree"], out["overall"]["column_yes"]["rows"]) == (76, 95)
    assert (out["overall"]["column_no"]["agree"], out["overall"]["column_no"]["rows"]) == (86, 136)


async def test_per_stratum_lowest_first_each_with_its_counts_and_interval(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    set_id, version = made
    out = await _read(_arguments(set_id, version, by=["kind"]))
    by_kind = {s["values"]["kind"]: s for s in out["strata"]}
    assert set(by_kind) == {"pos", "other_number", "masked"}
    assert [s["values"]["kind"] for s in out["strata"]] == ["masked", "pos", "other_number"]
    assert (by_kind["masked"]["agree"], by_kind["masked"]["rows"]) == (0, 40)
    assert (by_kind["pos"]["agree"], by_kind["pos"]["rows"]) == (76, 95)
    assert (by_kind["other_number"]["agree"], by_kind["other_number"]["rows"]) == (86, 96)
    low, high = wilson_by_quadratic(76, 95)
    assert by_kind["pos"]["ci_low"] == pytest.approx(low)
    assert by_kind["pos"]["ci_high"] == pytest.approx(high)
    assert out["strata_total"] == 3 and out["strata_truncated"] is False


async def test_strata_cross_and_a_cap_says_so(
    client: httpx.AsyncClient, made: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_id, version = made
    out = await _read(_arguments(set_id, version, by=["kind", "framing"]))
    assert out["strata_total"] == 6
    cell = next(
        s for s in out["strata"] if s["values"] == {"kind": "pos", "framing": "named_person"}
    )
    assert cell["rows"] == 47  # the odd-numbered of 95
    monkeypatch.setattr(label_agreement, "MAX_STRATA", 2)
    cut = await _read(_arguments(set_id, version, by=["kind", "framing"]))
    assert len(cut["strata"]) == 2 and cut["strata_truncated"] is True
    assert cut["strata_total"] == 6


async def test_one_split_reads_only_that_split(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    set_id, version = made
    splits, answers = reference()
    out = await _read(_arguments(set_id, version, split="test"))
    in_test = [r for r in splits["test"] if row_key(r["text"]) in answers]
    assert out["labelled_rows_found"] == len(in_test) < 231
    assert out["labelled_rows_not_found"] == 300 - len(in_test)
    expected = sum(
        (r["deployed_truth"] is True) == (answers[row_key(r["text"])] == YES) for r in in_test
    )
    assert out["overall"]["agree"] == expected and out["overall"]["rows"] == len(in_test)


async def test_the_decoy_label_gives_another_answer(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    set_id, version = made
    true_column = await _read(_arguments(set_id, version))
    decoy = await _read(
        _arguments(set_id, version, label_column="label", positive_value="negative")
    )
    # the decoy's negative is deployed_truth's true, so reading it this way agrees exactly...
    assert decoy["overall"]["agree"] == true_column["overall"]["agree"]
    flipped = await _read(
        _arguments(set_id, version, label_column="label", positive_value="positive")
    )
    # ...and reading its positive as yes is the mirror image: a reader of the wrong column shows
    assert flipped["overall"]["agree"] == 231 - true_column["overall"]["agree"]


async def test_a_mistyped_positive_value_is_refused_not_counted_as_no(
    client: httpx.AsyncClient, made: tuple[str, str]
) -> None:
    """``True`` for a boolean column would call every row the column's no, and answer."""
    set_id, version = made
    with pytest.raises(ToolError) as exc:
        await _read(_arguments(set_id, version, positive_value="True"))
    code, status, details = refusal_of(exc.value)
    assert (code, status) == ("positive_value_not_found", 422)
    assert details["column_values"] == {"false": 136, "true": 95}
    assert details["next"]["tool"] == "dataworks_get_version_crosstab"


async def test_a_row_the_mapping_excluded_or_a_null_value_is_not_compared(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    rows = [
        {"text": "a", "truth": 1, "kind": "x"},
        {"text": "b", "truth": 0, "kind": "x"},
        {"text": "c", "truth": None, "kind": "x"},
        {"text": "d", "truth": 1, "kind": "x"},
    ]
    version = split_version(data_dir, {"train": rows})
    answers: dict[str, str | None] = {
        row_key("a"): YES,
        row_key("b"): YES,
        row_key("c"): YES,
        row_key("d"): None,
    }
    set_id = calibration_set(version, answers, label_set=(YES, NO))
    out = await _read(
        {
            "calibration_set_id": set_id,
            "version_id": version,
            "label_column": "truth",
            "positive_value": "1",
        }
    )
    assert out["labelled_rows"] == 3, "the row with no human label is not a label"
    assert out["rows_no_value"] == 1 and out["overall"]["rows"] == 2
    assert out["overall"]["column_yes"]["agree"] == 1
    assert out["overall"]["column_no"]["agree"] == 0


@pytest.mark.parametrize(
    ("change", "code", "status"),
    [
        ({"calibration_set_id": "cs_missing"}, "CALIBRATION_SET_NOT_FOUND", 404),
        ({"version_id": "not-a-version"}, "version_not_found", 404),
        ({"split": "nope"}, "split_not_found", 404),
        ({"label_column": "nope"}, "unknown_column", 422),
        ({"by": ["nope"]}, "unknown_column", 422),
        ({"exclude": {"nope": ["x"]}}, "unknown_column", 422),
    ],
)
async def test_refusals_are_named(
    client: httpx.AsyncClient,
    made: tuple[str, str],
    change: dict[str, Any],
    code: str,
    status: int,
) -> None:
    set_id, version = made
    with pytest.raises(ToolError) as exc:
        await _read({**_arguments(set_id, version), **change})
    assert refusal_of(exc.value)[:2] == (code, status)


async def test_each_set_reads_its_own_answers(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    """Two people, the same rows: one set's answers must not reach the other's figure. A row key is
    shared by design, so nothing but the set's id tells them apart."""
    rows = [{"text": f"row {i}", "truth": i % 2 == 0} for i in range(8)]
    version = split_version(data_dir, {"train": rows})
    agrees = {row_key(r["text"]): (YES if r["truth"] else NO) for r in rows}
    disagrees = {row_key(r["text"]): (NO if r["truth"] else YES) for r in rows}
    agreeing = calibration_set(version, agrees, label_set=(YES, NO))
    disagreeing = calibration_set(version, disagrees, label_set=(YES, NO))
    for set_id, expected in ((agreeing, 8), (disagreeing, 0)):
        out = await _read(
            {
                "calibration_set_id": set_id,
                "version_id": version,
                "label_column": "truth",
                "positive_value": "true",
            }
        )
        assert out["overall"]["agree"] == expected and out["labelled_rows"] == 8


async def test_a_set_of_more_than_two_labels_is_refused(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    version = split_version(data_dir, {"train": [{"text": "a", "truth": True}]})
    set_id = calibration_set(version, {row_key("a"): "x"}, label_set=("x", "y", "z"))
    got = await client.post(
        f"/api/v1/calibration-sets/{set_id}/label-agreement",
        json={"version_id": version, "label_column": "truth", "positive_value": "true"},
    )
    assert got.status_code == 422 and got.json()["error"]["code"] == "calibration_set_not_binary"


async def test_it_reads_in_its_own_session_off_the_greenlet_once_with_the_payload(
    client: httpx.AsyncClient, made: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Memory ``numpy-on-a-greenlet-segfaults``: the crash does not reproduce here, so the wiring is
    what is held: the top of a plain thread, one call, the arguments the tool was given."""
    set_id, version = made
    real = label_agreement.agreement
    calls: list[tuple[Any, ...]] = []

    def spy(session: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((getcurrent().parent is None, args, kwargs))
        return real(session, *args, **kwargs)

    monkeypatch.setattr(label_agreement, "agreement", spy)
    await _read(_arguments(set_id, version, split="train", by=["kind"], exclude={"kind": ["x"]}))
    assert calls == [
        (
            True,
            (set_id, version, "deployed_truth", "true"),
            {"split": "train", "by": ["kind"], "exclude": {"kind": ["x"]}},
        )
    ]
