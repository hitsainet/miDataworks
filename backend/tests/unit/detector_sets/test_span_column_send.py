"""The span column a send declares (ENH-006 G39; operator decision 2026-11-09): the capability read from
miWorkbench's served schema, the registration body, the column identity a reused view is matched by, and
the choice per role. The send end to end is ``tests/integration/detector_sets/test_send_span_column.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.clients import miworkbench_client as mc
from src.core.canonical_json import canonical_sha256
from src.services.detector_sets import capabilities, plan, span_column
from tests.support.fake_miworkbench import BASE, FakeMiWorkbench


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMiWorkbench:
    f = FakeMiWorkbench()
    monkeypatch.setattr(mc, "TRANSPORT", f.transport())
    capabilities.clear_cache()
    return f


def _read(fake: FakeMiWorkbench) -> capabilities.Capabilities:
    with mc.MiWorkbenchClient(BASE) as client:
        return capabilities.read(client, fresh=True)


def test_the_capability_is_the_name_on_probe_dataset_create(fake: FakeMiWorkbench) -> None:
    assert _read(fake).view_span_column is False
    fake.serves_span_column = True
    caps = _read(fake)
    assert (
        caps.view_span_column is True and caps.as_dict()["reasons"]["view_span_column"] == "served"
    )
    assert capabilities.WIRE_SPAN_COLUMN == "span_column"


def _body(**extra: object) -> dict[str, object]:
    return plan.registration_body(
        name="v",
        dataset_id="d",
        config=None,
        split="train",
        input_column="text",
        label_column="label",
        label_mapping={"a": "positive", "b": "negative"},
        role="train",
        pair_column=None,
        **extra,  # type: ignore[arg-type]
    )


def test_the_body_carries_it_only_when_served_and_named() -> None:
    assert _body(span_column="r_ssn_spans", span_column_served=True)["span_column"] == "r_ssn_spans"
    assert "span_column" not in _body(span_column="r_ssn_spans", span_column_served=False)
    assert "span_column" not in _body(span_column=None, span_column_served=True)
    assert "span_column" in plan.OPTIONAL_KEYS


def test_a_view_registered_before_g39_keeps_its_column_identity() -> None:
    """Every registration row written before G39 hashed exactly these three keys."""
    old = canonical_sha256({"input_column": "text", "label_column": "label", "pair_column": None})
    assert plan.columns_sha256("text", "label", None) == old
    assert plan.columns_sha256("text", "label", None, "r_ssn_spans") != old


def _split(tmp_path: Path, columns: dict[str, list[object]]) -> Path:
    path = tmp_path / "split.parquet"
    pq.write_table(pa.table(columns), path)
    return path


LISTED = [
    {"column": "r_ssn_spans", "text_column": "text", "unit": "code_points", "rule_set": "s@2"},
    {"column": "r_body_spans", "text_column": "body", "unit": "code_points", "rule_set": "b@1"},
    {"column": "r_more_spans", "text_column": "text", "unit": "code_points", "rule_set": "m@1"},
    {"column": "r_gone_spans", "text_column": "text", "unit": "code_points", "rule_set": "g@1"},
]


def test_the_first_column_over_the_roles_input_is_declared_and_the_rest_say_why(
    data_dir: Path,
) -> None:
    path = _split(
        data_dir,
        {
            c: [json.dumps([[0, 1]])]
            for c in ("text", "body", "r_ssn_spans", "r_body_spans", "r_more_spans")
        },
    )
    chosen = span_column.choose(path, LISTED, "text")
    assert chosen.column == "r_ssn_spans"
    assert chosen.left_out == {
        "r_body_spans": "its offsets index 'body', and this role's input column is 'text'",
        "r_more_spans": "miWorkbench takes one span column per view; 'r_ssn_spans' is declared",
        "r_gone_spans": "not in this split",
    }


def test_a_role_over_another_text_declares_none(data_dir: Path) -> None:
    path = _split(data_dir, {"prompt": ["x"], "r_ssn_spans": ["[]"]})
    chosen = span_column.choose(path, LISTED[:1], "prompt")
    assert chosen.column is None and "r_ssn_spans" in chosen.left_out


def test_nothing_listed_nothing_declared(tmp_path: Path) -> None:
    assert span_column.choose(tmp_path / "unread.parquet", [], "text") == span_column.Chosen()
