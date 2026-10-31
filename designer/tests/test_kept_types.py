"""Declared types are kept between Data Designer steps, through the REAL library (DLP run F1,
2026-10-21).

The e2e DLP run's gate showed it: Data Designer ``json.loads`` every string of a record before a
template sees it, so a ``dtype: str`` column holding ``"9"`` reached the next step as the int 9
(``vn != '9'`` was always true), ``"[ ]"`` as an empty list, ``"269865658"`` as an int
(``val|length`` -> ``render_error``, rows dropped) and JSON text as a dict, while
``"036001808"`` (not valid JSON) stayed text: one column, mixed types, and a build reported
"completed" with every SSN gate row labelled ``none``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import dd_paths
from src.operators.data_designer import runner

CATALOGUE = json.loads(dd_paths.CATALOGUE.read_text())
TEXTS = ["9", "[ ]", '{"a": 1}', "true", "036001808", "null", "269865658", "1e5"]


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


def _payload(expr: str, *, dtype: str = "str", visible: list[str]) -> dict[str, Any]:
    entry = next(
        o for o in CATALOGUE["operators"] if o["manifest"]["name"] == "dd_expression"
    )
    return {
        "operator": "dd_expression@x",
        "column_class": entry["column_class"],
        "column_fields": entry["column_fields"],
        "params": {"expr": expr, "dtype": dtype},
        "params_schema": entry["manifest"]["params_schema"],
        "output_column": "out",
        "seed_columns": ["text"],
        "visible_columns": visible,
        "endpoint": None,
        "key_ref": None,
    }


def _table(values: list[Any]) -> pa.Table:
    return pa.table(
        {
            "text": [f"row {i}" for i in range(len(values))],
            "v": values,
            "_dw_row_key": [f"{i:064x}" for i in range(len(values))],
            "_dw_occurrence": pa.array([0] * len(values), pa.int32()),
        }
    )


def test_a_string_column_reaches_the_template_as_a_string() -> None:
    expr = "{% if v is string %}S:{{ v }}:{{ v|length }}{% else %}COERCED{% endif %}"
    produced, _ = runner.generate(
        _table(TEXTS), _payload(expr, visible=["text", "v"]), None
    )
    assert [r["out"] for r in produced] == [f"S:{t}:{len(t)}" for t in TEXTS]


def test_a_chain_of_two_steps_keeps_the_first_steps_text(data_dir: Path) -> None:
    """Step 1 renders text that LOOKS like a number; step 2 must see text."""
    (data_dir / "in").mkdir()
    pq.write_table(_table(TEXTS), data_dir / "in" / "part-00000.parquet")
    step1 = {
        **_payload("{{ v }}", visible=["text", "v"]),
        "input_dir": "in",
        "output_dir": "runs/j/a.dd",
    }
    runner.run_step(step1)
    out1 = pq.read_table(data_dir / "runs" / "j" / "a.dd" / "output.parquet")
    assert out1.schema.field("out").type == pa.string()
    joined = _table(TEXTS).append_column("first", out1.column("out"))
    (data_dir / "mid").mkdir()
    pq.write_table(joined, data_dir / "mid" / "part-00000.parquet")
    step2 = {
        **_payload(
            "{% if first == '9' %}nine{% elif first is string %}text{% else %}COERCED{% endif %}",
            visible=["text", "first"],
        ),
        "input_dir": "mid",
        "output_dir": "runs/j/b.dd",
    }
    runner.run_step(step2)
    out2 = pq.read_table(data_dir / "runs" / "j" / "b.dd" / "output.parquet").column(
        "out"
    )
    assert out2.to_pylist() == ["nine"] + ["text"] * (len(TEXTS) - 1)


def test_a_declared_int_is_stored_and_read_as_an_int(data_dir: Path) -> None:
    (data_dir / "in").mkdir()
    pq.write_table(_table(TEXTS), data_dir / "in" / "part-00000.parquet")
    payload = {
        **_payload("{{ text|length }}", dtype="int", visible=["text"]),
        "input_dir": "in",
        "output_dir": "runs/j/c.dd",
    }
    runner.run_step(payload)
    out = pq.read_table(data_dir / "runs" / "j" / "c.dd" / "output.parquet")
    assert out.schema.field("out").type == pa.int64()
    assert out.column("out").to_pylist() == [len(f"row {i}") for i in range(len(TEXTS))]


def test_a_value_of_another_type_fails_loudly() -> None:
    with pytest.raises(runner.DtypeViolation, match="dtype_violation"):
        runner.check_dtypes([{"_dw_row_key": "k", "out": 9}], "out", "str")
    with pytest.raises(runner.DtypeViolation):
        runner.check_dtypes([{"_dw_row_key": "k", "out": True}], "out", "int")
    runner.check_dtypes(
        [{"_dw_row_key": "k", "out": "9"}, {"_dw_row_key": "l", "out": None}],
        "out",
        "str",
    )


def test_the_guard_refuses_to_run_when_data_designer_moved_the_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    module = importlib.import_module(runner.STRING_GUARD_MODULES[0])
    monkeypatch.delattr(module, "deserialize_json_values")
    with pytest.raises(RuntimeError, match="cannot be installed"):
        runner.install_string_guard()


def test_a_models_structured_output_is_still_parsed() -> None:
    """The guard covers RECORDS only: a structured LLM answer (a string) is still deserialised."""
    runner.install_string_guard()
    import importlib

    module = importlib.import_module(runner.STRING_GUARD_MODULES[1])
    assert module.deserialize_json_values('{"a": 1}') == {"a": 1}
    assert module.deserialize_json_values({"v": "9"}) == {"v": "9"}
