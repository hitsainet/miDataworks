"""Data Designer's template rules, column visibility and per-row drop reasons, through the REAL
library (PII run G4, G5/L2 and F-3, 2026-10-20).

- G4: ``runner.validate_templates`` names the rule Data Designer would refuse on (the run learned
  them by reading Data Designer's source inside the pod).
- G5: a template may name a metadata column or an earlier step's output; before, only content
  columns were seeded and ``{{ uid }}`` failed as an unknown reference.
- F-3: a row an expression renders empty for is ``empty_render``, not a generation failure.
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


def _entry(name: str) -> dict[str, Any]:
    return next(o for o in CATALOGUE["operators"] if o["manifest"]["name"] == name)


def _validate(expr: str, columns: list[str] | None = None) -> dict[str, Any]:
    entry = _entry("dd_expression")
    return runner.validate_templates(
        {
            "column_class": entry["column_class"],
            "column_fields": entry["column_fields"],
            "params": {"expr": expr, "dtype": "str"},
            "output_column": "expression",
            "columns": columns,
        }
    )


def _rules(answer: dict[str, Any]) -> list[str]:
    return [e["rule"] for e in answer["errors"]]


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path


# --- G4: the rules, named -----------------------------------------------------------------------


def test_a_valid_template_is_valid_and_lists_its_references() -> None:
    answer = _validate("{{ text | upper }}", ["text"])
    assert answer["valid"] is True and answer["errors"] == []
    assert answer["references"] == {"expr": ["text"]}
    assert answer["limits"]["max_nodes"] == 600 and answer["limits"]["max_depth"] == 10


@pytest.mark.parametrize(
    ("expr", "rule"),
    [
        ("{% set x = 1 %}{{ x }}", "forbidden_tag"),
        ("{% for a in text %}{% for b in text %}{{ b }}{% endfor %}{% endfor %}", "nested_for"),
        ("{{ text | wordcount }}", "forbidden_filter"),
        ("{{ text | join(',') }}", "forbidden_filter"),
        ("{{ self }}", "self_reference"),
        ("{{ text", "syntax"),
        ("{% if text %}" * 9 + "{{ text }}" + "{% endif %}" * 9, "too_deep"),
    ],
)
def test_each_refused_template_names_its_rule(expr: str, rule: str) -> None:
    answer = _validate(expr, ["text"])
    assert answer["valid"] is False
    assert rule in _rules(answer), answer["errors"]


def test_too_many_nodes_names_the_count_and_the_limit() -> None:
    expr = " ".join("{{ text }}" for _ in range(320))
    answer = _validate(expr, ["text"])
    (error,) = [e for e in answer["errors"] if e["rule"] == "too_many_nodes"]
    assert error["limit"] == 600 and error["found"] > 600


def test_an_unknown_reference_names_what_the_step_sees() -> None:
    answer = _validate("{{ uid[-1] }}", ["text"])
    (error,) = answer["errors"]
    assert error["rule"] == "unknown_reference" and error["found"] == ["uid"]


def test_every_template_data_designer_refuses_is_refused_here() -> None:
    """The authority check: whatever our named rules pass, Data Designer's own validator and a
    compile must pass too (a rule we do not name is reported as ``data_designer``)."""
    from data_designer.engine.processing.ginja.environment import UserTemplateSandboxEnvironment

    for expr in ("{{ text | upper }}", "{% if text %}{{ text }}{% endif %}", "{{ text[:3] }}"):
        env = UserTemplateSandboxEnvironment(allowed_references=["text"])
        env.validate_template(expr)
        assert _validate(expr, ["text"])["valid"] is True


# --- G5: visibility -----------------------------------------------------------------------------


def _payload(expr: str, *, visible: list[str] | None) -> dict[str, Any]:
    entry = _entry("dd_expression")
    payload: dict[str, Any] = {
        "operator": "dd_expression@x",
        "column_class": entry["column_class"],
        "column_fields": entry["column_fields"],
        "params": {"expr": expr, "dtype": "str"},
        "params_schema": entry["manifest"]["params_schema"],
        "output_column": "expression",
        "seed_columns": ["text"],
        "endpoint": None,
        "key_ref": None,
    }
    if visible is not None:
        payload["visible_columns"] = visible
    return payload


def _table() -> pa.Table:
    return pa.table(
        {
            "text": ["abc", "skip", "de"],
            "uid": ["u-1", "u-2", "u-3"],
            "stage_a": ["A1", "A2", "A3"],
            "_dw_row_key": [f"{i:064x}" for i in range(3)],
            "_dw_occurrence": pa.array([0, 0, 0], pa.int32()),
        }
    )


def test_a_template_may_name_a_metadata_column_and_an_earlier_steps_output() -> None:
    payload = _payload("{{ uid[-1] }}-{{ stage_a }}", visible=["stage_a", "text", "uid"])
    produced, _ = runner.generate(_table(), payload, None)
    assert [r["expression"] for r in produced] == ["1-A1", "2-A2", "3-A3"]


def test_without_visible_columns_only_content_is_seeded() -> None:
    """The old payload (no ``visible_columns``) keeps the content-only behaviour: the negative
    control for the visibility change."""
    payload = _payload("{{ uid }}", visible=None)
    assert runner.visible_seed_columns(_table(), payload) == ["text"]
    with pytest.raises(Exception):  # noqa: B017 - Data Designer refuses the unknown reference
        runner.generate(_table(), payload, None)


def test_system_columns_are_never_visible() -> None:
    payload = _payload("{{ _dw_row_key }}", visible=["text", "_dw_row_key"])
    assert runner.visible_seed_columns(_table(), payload) == ["text"]


# --- F-3: why a row produced nothing ------------------------------------------------------------


def test_an_empty_render_is_its_own_reason_and_reaches_the_step_output(data_dir: Path) -> None:
    (data_dir / "in").mkdir()
    pq.write_table(_table(), data_dir / "in" / "part-00000.parquet")
    expr = "{% if text != 'skip' %}{{ text }}{% endif %}"
    payload = {
        **_payload(expr, visible=["stage_a", "text", "uid"]),
        "input_dir": "in",
        "output_dir": "runs/j/s.dd",
    }
    assert runner.run_step(payload)["produced"] == 2
    drops = json.loads((data_dir / "runs" / "j" / "s.dd" / runner.DROPS_FILE).read_text())
    assert drops == {f"{1:064x}|0": "empty_render"}


def test_a_failed_cast_is_not_an_empty_render() -> None:
    payload = {
        **_payload("{{ text }}", visible=["text"]),
        "params": {"expr": "{{ text }}", "dtype": "int"},
    }
    table = _table()
    produced = [{"_dw_row_key": f"{0:064x}", "_dw_occurrence": 0, "expression": 1}]
    reasons = runner.classify_drops(table, payload, produced)
    assert set(reasons.values()) == {"type_cast_error"}


def test_a_whitespace_render_is_an_empty_render_too() -> None:
    """Data Designer's expression generator drops a value that is blank after ``strip`` although
    its renderer accepted it; that row is ``empty_render`` as well."""
    expr = "{% if text != 'skip' %}{{ text }}{% else %}   {% endif %}"
    payload = _payload(expr, visible=["text"])
    produced, _ = runner.generate(_table(), payload, None)
    assert [r["expression"] for r in produced] == ["abc", "de"]
    assert runner.classify_drops(_table(), payload, produced) == {f"{1:064x}|0": "empty_render"}
