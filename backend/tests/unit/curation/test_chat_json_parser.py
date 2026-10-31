"""``chat_json_parser`` on the real shapes of ``Arrrlex/models-under-pressure`` (2026-10-08 finding 1).

Every chat in that dataset is a JSON string. The normaliser drops a JSON string as ``unmappable``,
so a probe run sent the raw characters to miLLM as one user turn. These tests use the rows as they
are on the Hub, never a list built to agree with the parser.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from src.operators.native.curation.chat_json_parser import MESSAGES_TYPE, parse_chat_json
from src.services.row_keys import ROWKEY_V1, compute_row_key
from tests.fixtures import models_under_pressure as mup
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

ROLES = {"inputs": "content", "labels": "metadata"}


def _table(values: list[str | None]) -> pa.Table:
    rows = [{"inputs": v, "labels": f"l{i}"} for i, v in enumerate(values)]
    return with_system_columns(rows, content=("inputs",))


def _real_table() -> pa.Table:
    rows = [{"inputs": r["inputs"], "labels": r["labels"]} for r in mup.mental_health_rows()]
    return with_system_columns(rows, content=("inputs",))


class TestAccept:
    def test_every_real_mental_health_row_parses_into_its_messages(self, data_dir: Path) -> None:
        table = _real_table()
        ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, table, ROLES)
        assert ran.output.num_rows == table.num_rows
        assert ran.output.schema.field("inputs").type == MESSAGES_TYPE
        parsed = ran.output.column("inputs").to_pylist()
        for raw, messages in zip(table.column("inputs").to_pylist(), parsed, strict=True):
            assert messages == json.loads(raw)  # verbatim: roles, content, order
            assert [m["role"] for m in messages] == ["system", "user"]

    def test_a_parsed_row_is_changed_and_rekeyed_over_the_messages(self, data_dir: Path) -> None:
        table = _real_table()
        ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, table, ROLES)
        changed = ran.events_by_reason("parsed_json_chat")
        assert len(changed) == table.num_rows
        out = ran.output.to_pylist()
        for row in out:
            assert row["_dw_row_key"] == compute_row_key(
                {"inputs": row["inputs"]}, ["inputs"], ROWKEY_V1
            )
        # Identical chats still share one key (ADR-005: the key is the content): the four "nan"
        # rows are one key with occurrences 0..3.
        nan_keys = {r["_dw_row_key"] for r in out if r["inputs"][-1]["content"] == mup.NAN_TURN}
        assert len(nan_keys) == 1
        occ = sorted(r["_dw_occurrence"] for r in out if r["inputs"][-1]["content"] == mup.NAN_TURN)
        assert occ == [0, 1, 2, 3]

    def test_a_tool_turn_from_toolace_is_kept_as_a_tool_turn(self, data_dir: Path) -> None:
        raw = mup.shape("toolace_balanced_test_with_tool_turn")["inputs"]
        ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, _table([raw]), ROLES)
        [messages] = ran.output.column("inputs").to_pylist()
        assert "tool" in [m["role"] for m in messages]
        assert messages == json.loads(raw)

    def test_sharegpt_from_value_inside_json_text_is_read(self) -> None:
        raw = json.dumps(
            [{"from": "human", "value": "hi\n  there"}, {"from": "gpt", "value": "ok"}]
        )
        assert parse_chat_json(raw, "c", "drop") == [
            {"role": "user", "content": "hi\n  there"},
            {"role": "assistant", "content": "ok"},
        ]

    def test_a_json_string_is_one_user_turn_only_when_asked(self) -> None:
        raw = mup.shape("training_train_json_string")["inputs"]
        assert parse_chat_json(raw, "c", "user_turn") == [
            {"role": "user", "content": json.loads(raw)}
        ]


class TestDropWithReason:
    @pytest.mark.parametrize(
        ("value", "code", "says"),
        [
            ("nan", "chat_json_unparseable", "not valid JSON"),
            (None, "chat_json_unparseable", "missing"),
            ("   ", "chat_json_unparseable", "empty"),
            ('{"role": "user", "content": "x"}', "chat_json_not_a_chat", "not a list"),
            ("[]", "chat_json_not_a_chat", "non-empty list"),
            ('[{"role": "narrator", "content": "x"}]', "chat_json_not_a_chat", "not recognised"),
            ('[{"role": "user", "content": null}]', "chat_json_not_a_chat", "content"),
            (
                '[{"role": "assistant", "content": "x", "tool_calls": []}]',
                "chat_json_not_a_chat",
                "tool_calls",
            ),
        ],
    )
    def test_each_kind_of_non_chat_is_dropped_naming_it(
        self, data_dir: Path, value: str | None, code: str, says: str
    ) -> None:
        good = mup.mental_health_rows()[0]["inputs"]
        ran = run_operator(
            "chat_json_parser", {"columns": ["inputs"]}, _table([value, good]), ROLES
        )
        dropped = ran.events_by_reason(code)
        assert len(dropped) == 1, ran.events.to_pylist()
        assert says in dropped[0]["reason"]
        assert dropped[0]["statistic_text"].startswith("inputs")
        assert ran.output.num_rows == 1  # the good row is kept, the bad one is not guessed

    def test_a_json_string_is_dropped_by_default(self, data_dir: Path) -> None:
        raw = mup.shape("training_train_json_string")["inputs"]
        ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, _table([raw]), ROLES)
        [dropped] = ran.events_by_reason("chat_json_not_a_chat")
        assert "JSON string" in dropped["reason"] and "user_turn" in dropped["reason"]
        assert ran.output.num_rows == 0

    def test_plain_prose_is_not_read_as_a_chat(self, data_dir: Path) -> None:
        raw = mup.shape("training_train_plain_text")["inputs"]
        ran = run_operator("chat_json_parser", {"columns": ["inputs"]}, _table([raw]), ROLES)
        assert len(ran.events_by_reason("chat_json_unparseable")) == 1
        assert ran.output.num_rows == 0

    def test_a_column_that_already_holds_messages_is_refused_naming_the_normaliser(
        self, data_dir: Path
    ) -> None:
        rows = [{"inputs": [{"role": "user", "content": "x"}], "labels": "a"}]
        table = with_system_columns(rows, content=("inputs",))
        with pytest.raises(StepFailed) as raised:
            run_operator("chat_json_parser", {"columns": ["inputs"]}, table, ROLES)
        assert "normaliser" in str(raised.value)

    def test_a_missing_column_is_refused(self, data_dir: Path) -> None:
        with pytest.raises(StepFailed):
            run_operator("chat_json_parser", {"columns": ["nope"]}, _table(["[]"]), ROLES)
