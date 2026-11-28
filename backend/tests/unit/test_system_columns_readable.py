"""System columns are read-only, everywhere a step reads a column (MCP gap G-4, FU-7a 2026-11-25).

FU-7a's reserve recipe: ``rule_label@2`` and ``regex_match@2`` refused ``_dw_split`` and
``_dw_source_locator`` at plan time with ``missing_input_column`` ("It has: text"), while
``metadata_value_filter@1.0.0`` accepted the same names (it declared no reads, so nothing checked
it) and they worked at build time. Now every reader may name a system column; no step may write a
``_dw_`` column (``system_column_write``, at validation and in the worker); a ``_dw_`` name that is
not a system column is ``unknown_system_column``; a Data Designer template, which never sees
system columns, is refused ``system_column_not_visible`` naming the column.

Validation runs against the LIVE registry, with the inputs' columns as ``plan_build`` passes them
(system columns taken out) and with none (saving a recipe).
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from src.operators import effects
from src.operators.errors import STATUS_FOR_CODE, StepFailed
from src.operators.protocol import OperatorResult
from src.services.recipe_service import validate_body
from src.services.step_contract import SYSTEM_COLUMNS
from tests.support.curation_fixtures import registry

#: The inputs' columns as ``build_plan_service.plan_build`` computes them: no ``_dw_`` column.
INPUT = {"text"}


@pytest.fixture(autouse=True)
def live_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import operator_port

    monkeypatch.setattr(operator_port, "_registry", registry())


def body(*steps: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "format": "dw.recipe/v1",
        "steps": [{"operator": o, "version": v, "params": p} for o, v, p in steps],
    }


def codes(raw: dict[str, Any], columns: set[str] | None = INPUT) -> list[list[str]]:
    result, _ = validate_body(raw, set(columns) if columns is not None else None)
    return [[e.code for e in s.errors] for s in result.steps]


RULE_ON_SPLIT = (
    "rule_label",
    "2",
    {"output_column": "fu7a_pick", "rules": ["fresh if _dw_split == everyday_fresh"]},
)
REGEX_ON_LOCATOR = (
    "regex_match",
    "2",
    {
        "column": "_dw_source_locator",
        "pattern": "^train:",
        "output_prefix": "fk_",
        "fold_fullwidth_ascii": False,
    },
)
FILTER_ON_OCCURRENCE = (
    "metadata_value_filter",
    "1.0.0",
    {"column": "_dw_occurrence", "values": ["1"]},
)
SAMPLE_BY_SPLIT = (
    "sample",
    "1.1.0",
    {"mode": "stratified", "strata": ["_dw_split"], "per_stratum": 2000},
)


@pytest.mark.parametrize(
    "step",
    [RULE_ON_SPLIT, REGEX_ON_LOCATOR, FILTER_ON_OCCURRENCE, SAMPLE_BY_SPLIT],
    ids=["rule_label", "regex_match", "metadata_value_filter", "sample_strata"],
)
def test_every_reader_accepts_a_system_column(step: tuple[str, str, dict[str, Any]]) -> None:
    assert codes(body(step)) == [[]]


def test_every_system_column_is_readable() -> None:
    for column in SYSTEM_COLUMNS:
        rule = ("rule_label", "2", {"output_column": "seen", "rules": [f"x if {column} is null"]})
        assert codes(body(rule)) == [[]], column


def test_the_fu7a_revision_steps_validate_without_a_helper_version() -> None:
    """Revisions 2 to 5 failed here; this is revision 6 with ``fk_matched`` replaced by
    ``_dw_split`` (no helper version), checked against the inputs' columns."""
    steps = body(
        FILTER_ON_OCCURRENCE,
        ("metadata_value_filter", "1.0.0", {"column": "_dw_source_locator", "values": ["train:7"]}),
        SAMPLE_BY_SPLIT,
        RULE_ON_SPLIT,
    )
    assert codes(steps) == [[], [], [], []]


def test_the_filter_now_checks_the_column_it_reads() -> None:
    """The pre-existing half of G-4: ``metadata_value_filter`` declared no reads, so a missing
    column passed validation and failed only at the build."""
    missing = ("metadata_value_filter", "1.0.0", {"column": "nope", "values": ["x"]})
    assert codes(body(missing)) == [["missing_input_column"]]
    result, _ = validate_body(body(missing), set(INPUT))
    assert "_dw_split" in result.steps[0].errors[0].message  # names the readable system columns


def test_a_dw_name_that_is_not_a_system_column_names_itself() -> None:
    step = ("rule_label", "2", {"output_column": "x", "rules": ["a if _dw_spilt == train"]})
    result, _ = validate_body(body(step), set(INPUT))
    (error,) = result.steps[0].errors
    assert error.code == "unknown_system_column"
    assert "'_dw_spilt'" in error.message and "_dw_split" in error.message


@pytest.mark.parametrize("columns", [INPUT, None], ids=["inputs_known", "saving"])
@pytest.mark.parametrize(
    "step",
    [
        ("rule_label", "2", {"output_column": "_dw_split", "rules": ["x if text is null"]}),
        ("rule_label", "2", {"output_column": "_dw_mine", "rules": ["x if text is null"]}),
        (
            "regex_match",
            "2",
            {
                "column": "text",
                "pattern": "a",
                "output_prefix": "_dw_",
                "fold_fullwidth_ascii": False,
            },
        ),
    ],
    ids=["overwrite_split", "new_dw_column", "dw_prefix"],
)
def test_no_step_may_write_a_dw_column(
    step: tuple[str, str, dict[str, Any]], columns: set[str] | None
) -> None:
    (found,) = codes(body(step), columns)
    assert found and set(found) == {"system_column_write"}  # one per _dw_ output


# --- in the worker: a write the step did not declare ---------------------------------------------


def _table(extra: dict[str, list[Any]] | None = None) -> pa.Table:
    cols: dict[str, Any] = {"text": ["a"], "_dw_row_key": ["k"], "_dw_occurrence": [0]}
    cols.update(extra or {})
    return pa.table(cols)


def test_the_worker_refuses_a_new_dw_column() -> None:
    before = _table()
    with pytest.raises(StepFailed) as caught:
        effects.check("labeler", before.schema, OperatorResult(output=_table({"_dw_x": [1]})), "t")
    assert caught.value.code == "system_column_write"
    assert caught.value.details == {"columns": ["_dw_x"]}
    assert STATUS_FOR_CODE["system_column_write"] == 409


def test_the_worker_lets_a_step_carry_or_add_a_real_system_column() -> None:
    """``split`` appends ``_dw_split`` to input that lacks it; that is not a write of a new name."""
    before = _table()
    after = _table({"_dw_split": ["train"]})
    assert effects.check("selector", before.schema, OperatorResult(output=after), "t") == set()


# --- Data Designer: the one reader that cannot see them ------------------------------------------


def test_a_designer_template_naming_a_system_column_is_refused_before_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.operators.data_designer import validation
    from src.operators.registry import DD_CATALOGUE, OperatorRegistry

    reg = OperatorRegistry.build(
        native=None, catalogues=((DD_CATALOGUE, "data_designer"),), entry_points=()
    )
    entry = reg.entry("dd_expression", reg.current_version("dd_expression"))
    sent: list[Any] = []
    monkeypatch.setattr(validation, "send_validate", sent.append)
    with pytest.raises(validation.OperatorError) as caught:
        validation.check_templates(entry, {"expr": "{{ text }} {{ _dw_split }}"}, ["text"])
    assert caught.value.code == "system_column_not_visible"
    assert caught.value.details["columns"] == {"expr": ["_dw_split"]}
    assert "'_dw_split'" in caught.value.message
    assert sent == []  # refused before the worker is asked
    assert STATUS_FOR_CODE["system_column_not_visible"] == 422
    assert validation.system_references(entry, {"expr": "{{ my_dw_split }}"}) == {}
