"""Pure pieces of the 2026-10-08 live findings: the bar a verdict names, the input-column check,
the scoring-form comparison and the one count. Figures are production's own."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from src.services.detector_sets import key_labels
from src.services.detector_sets import probe_protocol as pp
from src.services.detector_sets import reproduction_links as rl
from tests.fixtures import models_under_pressure as mup

#: ``pr_19c0458256f8``'s ``decision.windows.all`` (miLLM, read 2026-10-08).
L11_ALL_BANDS = [
    {"threshold": 20.42040252685547, "max_tokens": 203, "min_tokens": 0},
    {"threshold": 21.949451446533203, "max_tokens": 339, "min_tokens": 204},
    {"threshold": 23.379390716552734, "max_tokens": 518, "min_tokens": 340},
    {"threshold": 37.74921798706055, "max_tokens": None, "min_tokens": 519},
]


def probe(window_bar: float, bands: list[dict[str, Any]] | None) -> pp.ProbeInfo:
    return pp.ProbeInfo(
        probe_id="pr_19c0458256f8",
        name="L11",
        hf_id="meta-llama/Llama-3.1-8B-Instruct",
        layer=11,
        scope="all",
        threshold=window_bar,
        threshold_revision=2,
        window_thresholds={"all": window_bar},
        rung=2,
        rung_language="detects on unseen tasks",
        armed=True,
        mistudio_probe_id="pm_dcc6b7e0a850",
        mistudio_run_id="pmr_416ce6b1ee63",
        load_dtype="bfloat16",
        length_bands={"all": bands} if bands is not None else {},
    )


class TestBarSource:
    def test_the_live_preflight_bar_is_named_as_length_band_0_to_203(self) -> None:
        bar = pp.bar_source(probe(25.289772033691406, L11_ALL_BANDS), "all", 20.42040252685547, 162)
        assert bar["kind"] == "length_band" and bar["band"] == {"min_tokens": 0, "max_tokens": 203}
        assert bar["label"] == "length band 0–203 tokens: 20.42; window 'all' bar: 25.29"

    def test_the_second_live_instance_107_83_beside_129_91(self) -> None:
        bands = [{"threshold": 129.912, "min_tokens": 0, "max_tokens": 203}]
        bar = pp.bar_source(probe(107.83, bands), "all", 129.912, 150)
        assert bar["label"] == "length band 0–203 tokens: 129.91; window 'all' bar: 107.83"

    def test_an_open_ended_band_reads_plus(self) -> None:
        bar = pp.bar_source(probe(25.29, L11_ALL_BANDS), "all", 37.74921798706055, 700)
        assert bar["label"].startswith("length band 519+ tokens: 37.75")

    def test_the_window_bar_itself_is_named_as_the_window_bar(self) -> None:
        bar = pp.bar_source(probe(25.289772033691406, None), "all", 25.289772033691406, 10)
        assert bar["kind"] == "window" and bar["label"] == "window 'all' bar: 25.29"

    def test_a_bar_nothing_describes_is_said_never_assumed(self) -> None:
        bar = pp.bar_source(probe(25.29, L11_ALL_BANDS), "all", 11.0, 10)
        assert bar["kind"] == "not_described" and "does not describe" in bar["label"]

    def test_bands_are_read_from_the_definition(self) -> None:
        definition = {"decision": {"windows": {"all": {"length_bands": L11_ALL_BANDS}}}}
        assert pp._length_bands(definition) == {"all": L11_ALL_BANDS}


class TestInputColumn:
    TEXT = pa.string()
    CHAT = pa.list_(pa.struct([("role", pa.string()), ("content", pa.string())]))

    def test_json_chat_text_sent_as_one_user_turn_is_refused_naming_the_parser(self) -> None:
        values = [r["inputs"] for r in mup.mental_health_rows()]
        with pytest.raises(pp.ProbeRefused) as refused:
            pp.check_input_column("inputs", self.TEXT, "one_user_turn", values)
        assert refused.value.code == "INPUT_IS_JSON_CHAT"
        assert "chat_json_parser" in refused.value.message
        assert refused.value.details["json_chats"] == len(values)

    def test_plain_text_passes(self) -> None:
        plain = mup.shape("training_train_plain_text")["inputs"]
        pp.check_input_column("text", self.TEXT, "one_user_turn", [plain, "nan"])

    def test_messages_on_a_text_column_and_text_on_a_chat_column_are_refused(self) -> None:
        with pytest.raises(pp.ProbeRefused) as a:
            pp.check_input_column("inputs", self.TEXT, "messages", [])
        assert a.value.code == "FIELD_MAP_INVALID" and "chat_json_parser" in a.value.message
        with pytest.raises(pp.ProbeRefused) as b:
            pp.check_input_column("inputs", self.CHAT, "one_user_turn", [])
        assert b.value.code == "FIELD_MAP_INVALID"
        pp.check_input_column("inputs", self.CHAT, "messages", [])


class TestScoringForm:
    VIEW = {"counts": {"kinds": {"json_messages": 540}}}

    SERVED = {"render_form": {"generation_prompt": True, "add_special_tokens": False}}

    def test_parsed_messages_against_decoded_chats_are_equal_by_render_rule(self) -> None:
        # Kinds compatible: the render form decides (served here), never the input kinds alone.
        form = rl.scoring_form(self.VIEW, None, "messages", self.SERVED)
        assert form["agreement"] == "equal_by_render_rule"
        assert "as the chat it holds" in form["millm"]["described_as"]

    def test_text_against_decoded_chats_still_differs(self) -> None:
        assert rl.scoring_form(self.VIEW, None, "text")["agreement"] == "differs"

    def test_messages_against_plain_text_still_differs(self) -> None:
        view = {"counts": {"kinds": {"plain": 10}}}
        assert rl.scoring_form(view, None, "messages")["agreement"] == "differs"


class TestKeySummary:
    def test_the_four_nan_rows_are_one_conflicting_key(self) -> None:
        keyed = [
            ("nan" if r in mup.nan_rows() else r["inputs"], r["labels"])
            for r in mup.mental_health_rows()
        ]
        mapped = [(k, "positive" if lab == "high-stakes" else "negative") for k, lab in keyed]
        out = key_labels.summary(mapped)
        assert (out["rows"], out["row_keys"], out["keys_with_copies"]) == (14, 11, 1)
        assert out["conflicting"] == {
            "count": 1,
            "rows": 4,
            "keys": [{"row_key": "nan", "positive": 2, "negative": 2}],
        }
        assert "2 positive, 2 negative" in str(key_labels.conflict_sentence(out))

    def test_copies_that_agree_are_not_conflicting(self) -> None:
        out = key_labels.summary([("a", "positive"), ("a", "positive"), ("b", "negative")])
        assert out["keys_with_copies"] == 1 and out["conflicting"]["count"] == 0
        assert key_labels.conflict_sentence(out) is None


class TestMcpRetryReason:
    """The MCP tools carry the deliberate retry (finding 3) to the REST body, once."""

    ARGS = {
        "input_version_id": "ver_1",
        "role": "probe",
        "field_map": {"messages": "inputs"},
        "probe_id": "pr_1",
        "reproduction_retry_reason": "miLLM redeployed",
    }

    def test_start_sends_the_reason(self) -> None:
        from tests.support.mcp_harness import build_harness, call_tool

        mcp, client = build_harness()
        call_tool(mcp, "dataworks_start_label_run", dict(self.ARGS))
        assert client.calls == [
            (
                "POST",
                "/label-runs",
                {
                    "json_body": {
                        "input_version_id": "ver_1",
                        "role": "probe",
                        "field_map": {"messages": "inputs"},
                        "transport": "single",
                        "probe": {"probe_id": "pr_1"},
                        "reproduction_retry_reason": "miLLM redeployed",
                    }
                },
            )
        ]

    def test_plan_sends_the_reason(self) -> None:
        import json

        from tests.support.mcp_harness import build_harness, call_tool

        mcp, client = build_harness()
        call_tool(mcp, "dataworks_plan_label_run", dict(self.ARGS))
        assert len(client.calls) == 1
        method, path, payload = client.calls[0]
        assert (method, path) == ("GET", "/label-runs/plan")
        assert json.loads(payload["request"])["reproduction_retry_reason"] == "miLLM redeployed"
