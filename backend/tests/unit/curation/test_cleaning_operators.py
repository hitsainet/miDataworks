"""Cleaning operators through 003's real executor, and the TRL rules (FTASKS 8.1–8.3, 8.5–8.7)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from src.services.curation import trl_rules
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

TEXT = {"text": "content", "note": "metadata"}
CHAT = {"messages": "content", "note": "metadata"}


def _table(texts: list[str], splits: list[str] | None = None) -> pa.Table:
    t = with_system_columns([{"text": x, "note": f"n{i}"} for i, x in enumerate(texts)])
    if splits:
        t = t.set_column(t.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits))
    return t


def _chat(rows: list[Any]) -> pa.Table:
    from src.services.row_keys import ROWKEY_V1, compute_row_keys

    table = pa.Table.from_pylist([{"messages": m, "note": f"n{i}"} for i, m in enumerate(rows)])
    keys = compute_row_keys(table, ["messages"], ROWKEY_V1)
    return table.append_column("_dw_row_key", pa.array(list(keys))).append_column(
        "_dw_occurrence", pa.array([0] * len(rows), pa.int32())
    )


class TestNormaliser:
    def test_code_and_newlines_are_kept_verbatim(self, data_dir: Path) -> None:
        code = "def f(x):\n    return x  # <|user|>\n\nprint(f(1))"
        rows = [[{"role": "user", "content": code}, {"role": "assistant", "content": "ok\n"}]]
        ran = run_operator("normaliser", {"chat_columns": ["messages"]}, _chat(rows), CHAT)
        assert ran.events.num_rows == 0
        assert ran.output.column("messages").to_pylist()[0][0]["content"] == code

    def test_from_value_form_is_converted_with_a_changed_event(self, data_dir: Path) -> None:
        rows = [[{"from": "human", "value": "hi\nthere"}, {"from": "gpt", "value": "hello"}]]
        ran = run_operator("normaliser", {}, _chat(rows), CHAT)
        out = ran.output.column("messages").to_pylist()[0]
        assert [m["role"] for m in out] == ["user", "assistant"] and out[0][
            "content"
        ] == "hi\nthere"
        changed = ran.events_by_reason("normalised")
        assert len(changed) == 1 and changed[0]["statistic_text"] == "messages"

    def test_unmappable_row_is_dropped_naming_the_field(self, data_dir: Path) -> None:
        rows = [[{"role": "narrator", "content": "x"}], [{"role": "user", "content": "fine"}]]
        ran = run_operator("normaliser", {}, _chat(rows), CHAT)
        dropped = ran.events_by_reason("unmappable")
        assert len(dropped) == 1 and dropped[0]["statistic_text"] == "messages[0].role"
        assert ran.output.num_rows == 1

    def test_renaming_a_content_column_is_refused(self, data_dir: Path) -> None:
        with pytest.raises(StepFailed):
            run_operator("normaliser", {"rename": ["text=prompt"]}, _table(["a"]), TEXT)


class TestDedupExact:
    def test_exact_duplicates_keep_the_lowest_key_and_name_it(self, data_dir: Path) -> None:
        ran = run_operator("dedup_exact", {}, _table(["Same text", "Same text", "Other"]), TEXT)
        dropped = ran.events_by_reason("exact_duplicate")
        assert len(dropped) == 1 and dropped[0]["related_row_key"] == dropped[0]["row_key"]
        assert dropped[0]["occurrence"] == 1  # (key, 0) is kept: the lowest occurrence
        assert dropped[0]["statistic_name"] == "comparison_key"

    def test_case_and_spacing_variants_are_not_duplicates_by_default(self, data_dir: Path) -> None:
        texts = ["Trump 's tax plan", "Trump  's tax plan", "trump 's tax plan"]
        assert run_operator("dedup_exact", {}, _table(texts), TEXT).events.num_rows == 0

    def test_whitespace_collapse_is_a_parameter(self, data_dir: Path) -> None:
        texts = ["Trump 's tax plan", "Trump  's   tax plan"]
        ran = run_operator("dedup_exact", {"collapse_whitespace": True}, _table(texts), TEXT)
        assert len(ran.events_by_reason("exact_duplicate")) == 1
        kept = ran.output.column("text").to_pylist()
        assert kept == [min(texts, key=lambda t: _table([t]).column("_dw_row_key")[0].as_py())]

    def test_keep_rule_names_the_row_actually_kept_and_it_is_the_lowest_key(
        self, data_dir: Path
    ) -> None:
        # Three spacing variants: three DIFFERENT row keys, one comparison key (collapsed).
        texts = ["a  b c", "a b  c", "a b c"]
        table = _table(texts)
        ran = run_operator("dedup_exact", {"collapse_whitespace": True}, table, TEXT)
        kept = ran.output.column("_dw_row_key").to_pylist()
        assert kept == [min(table.column("_dw_row_key").to_pylist())]
        dropped = ran.events_by_reason("exact_duplicate")
        assert len(dropped) == 2
        assert {e["related_row_key"] for e in dropped} == set(kept)

    def test_cross_split_groups_reported_not_dropped(self, data_dir: Path) -> None:
        sink: list[dict] = []
        table = _table(["dup", "dup", "solo"], ["train", "test", "train"])
        ran = run_operator("dedup_exact", {}, table, TEXT, report_sink=sink)
        assert ran.events.num_rows == 0 and ran.output.num_rows == 3
        assert sink[0]["cross_split_groups"][0]["splits"] == ["test", "train"]


class TestFilters:
    def test_length_band_drops_both_sides_with_statistic(self, data_dir: Path) -> None:
        ran = run_operator(
            "length_band",
            {"column": "text", "min_length": 3, "max_length": 10},
            _table(["ab", "abcde", "abcdefghijklmnop"]),
            TEXT,
        )
        dropped = ran.events_by_reason("length_out_of_band")
        assert sorted(e["statistic_value"] for e in dropped) == [2.0, 16.0]
        assert ran.output.column("text").to_pylist() == ["abcde"]

    def test_length_band_in_words(self, data_dir: Path) -> None:
        ran = run_operator(
            "length_band",
            {"column": "text", "unit": "words", "min_length": 2, "max_length": 3},
            _table(["one", "one two", "a b c d"]),
            TEXT,
        )
        assert ran.output.column("text").to_pylist() == ["one two"]

    def test_constant_statistic_drops_nothing_inside_the_band(self, data_dir: Path) -> None:
        ran = run_operator(
            "length_band",
            {"column": "text", "min_length": 0, "max_length": 99},
            _table(["aaa", "bbb", "ccc"]),
            TEXT,
        )
        assert ran.events.num_rows == 0

    def test_empty_content_and_turn_band(self, data_dir: Path) -> None:
        ran = run_operator("empty_content", {}, _table(["  ", "x", "\n"]), TEXT)
        assert len(ran.events_by_reason("empty_content")) == 2
        rows = [
            [{"role": "user", "content": "a"}],
            [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        ]
        ran = run_operator("turn_count_band", {"min_turns": 2, "max_turns": 2}, _chat(rows), CHAT)
        assert ran.events.num_rows == 2

    def test_ngram_repetition(self, data_dir: Path) -> None:
        texts = ["buy now buy now buy now buy now", "a quick brown fox jumps over"]
        ran = run_operator(
            "ngram_repetition", {"column": "text", "n": 2, "max_ratio": 0.5}, _table(texts), TEXT
        )
        assert ran.output.column("text").to_pylist() == [texts[1]]

    def test_empty_batch_gives_empty_output_and_no_events(self, data_dir: Path) -> None:
        empty = _table(["x"]).slice(0, 0)
        for name, params in [
            ("length_band", {"column": "text"}),
            ("empty_content", {}),
            ("dedup_exact", {}),
            ("metadata_value_filter", {"column": "note", "values": ["a"]}),
        ]:
            ran = run_operator(name, params, empty, TEXT)
            assert ran.output.num_rows == 0 and ran.events.num_rows == 0, name


class TestTrl:
    def test_trl_pin_is_008s(self) -> None:
        if (
            importlib.util.find_spec("src.services.exports") is not None
            and importlib.util.find_spec("src.services.exports.trl_contracts") is not None
        ):
            from src.services.exports import trl_contracts

            assert trl_rules.TRL_VERSION is trl_contracts.TRL_VERSION
            assert trl_rules.PRM_TRL_VERSION is trl_contracts.PRM_TRL_VERSION
            assert set(trl_rules.VARIANTS) == set(trl_contracts.CONTRACTS)
            for name, contract in trl_contracts.CONTRACTS.items():
                assert trl_rules.VARIANTS[name] == tuple(v.columns for v in contract.variants)
        else:
            assert (trl_rules.TRL_VERSION, trl_rules.PRM_TRL_VERSION) == ("1.14.1", "0.29.1")

    def test_roles_alternate_and_no_empty_turn(self) -> None:
        ok = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
        assert trl_rules.chat_ok(ok).status == "ok"
        twice = [{"role": "user", "content": "u"}, {"role": "user", "content": "u"}]
        assert trl_rules.chat_ok(twice).status == "fail"
        empty = [{"role": "user", "content": " "}]
        assert "empty" in trl_rules.chat_ok(empty).message

    def test_dpo_rejected_empty_fails_and_context_never_passes(self) -> None:
        rules = trl_rules.rules_for("dpo", ("prompt", "chosen", "rejected"))
        out = trl_rules.check_row(rules, {"prompt": "p", "chosen": "c", "rejected": ""})
        status = {r.id: o.status for r, o in out}
        assert status["non_empty:rejected"] == "fail"
        assert status["context_window"] == "not_checked"

    def test_prm_lengths_and_kto_boolean(self) -> None:
        prm = trl_rules.rules_for("prm", ("prompt", "completions", "labels"))
        out = {
            r.id: o.status
            for r, o in trl_rules.check_row(
                prm, {"prompt": "p", "completions": ["a", "b"], "labels": [True]}
            )
        }
        assert out["prm_lengths"] == "fail"
        kto = trl_rules.rules_for("kto", ("prompt", "completion", "label"))
        out = {
            r.id: o.status
            for r, o in trl_rules.check_row(kto, {"prompt": "p", "completion": "c", "label": 1})
        }
        assert out["boolean:label"] == "fail"

    def test_filter_mode_drops_with_rule_and_column(self, data_dir: Path) -> None:
        table = with_system_columns(
            [{"prompt": "p", "chosen": "c", "rejected": r} for r in ("", "fine")],
            content=("prompt", "chosen", "rejected"),
        )
        ran = run_operator(
            "trl_validate",
            {"target_type": "dpo"},
            table,
            {"prompt": "content", "chosen": "content", "rejected": "content"},
        )
        dropped = ran.events_by_reason("trl_invalid")
        assert len(dropped) == 1 and dropped[0]["statistic_name"] == "non_empty:rejected"
        assert dropped[0]["statistic_text"] == "rejected"


@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("dedup_exact", {"collapse_whitespace": True}),
        ("length_band", {"column": "text", "min_length": 3, "max_length": 12}),
        ("empty_content", {}),
        ("ngram_repetition", {"column": "text"}),
        ("normaliser", {"chat_columns": []}),
    ],
)
def test_deterministic_for_one_seed(data_dir: Path, name: str, params: dict) -> None:
    table = _table(["a b", "a  b", "long enough text here", " ", "x x x x"])
    a = run_operator(name, params, table, TEXT, seed=9)
    b = run_operator(name, params, table, TEXT, seed=9)
    assert a.output.to_pylist() == b.output.to_pylist()
    assert a.events.to_pylist() == b.events.to_pylist()


def test_every_operator_declares_deterministic_truthfully() -> None:
    from src.operators.native.curation import CURATION_OPERATORS

    # No 004 operator in M1 calls a model; every one uses only the step seed.
    assert all(cls.manifest.deterministic for cls in CURATION_OPERATORS)
