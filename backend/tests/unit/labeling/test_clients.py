"""Protocol clients against the fake miLLM and TEI (005 FTASKS 6.1 – 6.7, 11.1 – 11.3)."""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from src.clients.endpoint_caller import EndpointCaller
from src.clients.endpoint_errors import ContextOverflow, ProtocolUnsupported
from src.clients.labelers import factory
from src.clients.labelers.base import ClassifierClient, RenderedInput
from src.clients.labelers.jev import noul_prompt
from src.clients.labelers.openai_chat_judge import OpenAIChatJudge, decide_structured_mode
from src.clients.labelers.openai_scoring import OpenAIScoringClient
from src.clients.labelers.plugins import PluginRefused, load_classifier
from src.clients.labelers.rubric_parsers import ParseFailure, json_v1, verdict_line_v1
from src.clients.labelers.tei import TEIClassifierClient, read_identity
from src.schemas.labeling import (
    OpenAIScoringTemplate,
    RubricBody,
    TEIClassificationTemplate,
)
from src.services.decision_template_service import builtin_template_documents
from tests.support.fake_millm import FakeMillm, FakeTEI

JEV: OpenAIScoringTemplate = builtin_template_documents()[0].body  # type: ignore[assignment]


def jevclient_body(model: str, state: str, question: str, config: dict[str, Any]) -> dict[str, Any]:
    """VERBATIM from scripts/jev_client.py JevClient.noul (the parity oracle for the body)."""
    start, end = config["slots"]["noul"]
    return {
        "model": model,
        "prompt": noul_prompt(state, question),
        "max_tokens": 1,
        "temperature": 1.0,
        "logprobs": end - start,
        "allowed_token_ids": config["verbalizer_ids"][start:end],
        "add_special_tokens": False,
        "return_tokens_as_token_ids": True,
    }


def row(text: str, question: str | None = "Is this text intended to be humorous?") -> RenderedInput:
    return RenderedInput("k" * 64, {"text": text}, question)


class TestCompletionsScoring:
    def test_request_body_is_the_prototypes_byte_for_byte(self, caller: EndpointCaller) -> None:
        client = OpenAIScoringClient(caller, JEV, "JEV-9B-decision")
        config = JEV.model_dump()
        mine = client.request_body(row("a joke"))
        theirs = jevclient_body(
            "JEV-9B-decision", "a joke", "Is this text intended to be humorous?", config
        )
        assert json.dumps(mine) == json.dumps(theirs)  # same keys, same ORDER, same values
        assert "top_logprobs" not in mine

    def test_score_reads_top_logprobs_and_records_provenance(
        self, caller: EndpointCaller, fakes: tuple[FakeMillm, FakeTEI]
    ) -> None:
        millm, _ = fakes
        millm.p_true = {"a joke": 0.8}
        result = OpenAIScoringClient(
            caller, JEV, "JEV-9B-decision", lease_id="lease-xyz-123"
        ).score(row("a joke"))
        (sent,) = millm.calls("/v1/completions")
        assert sent.headers["x-millm-strict"] == "true"
        assert sent.headers["x-millm-load-policy"] == "refuse"
        assert sent.headers["x-millm-lease"] == "lease-xyz-123"
        assert set(result.distribution) == {"false", "true"}
        assert math.isclose(sum(result.distribution.values()), 1.0)
        assert result.distribution["true"] > 0.75
        assert result.response_model == "JEV-9B-decision"
        assert result.system_fingerprint == millm.fingerprint
        assert result.steering_header is None
        assert result.request_body == sent.body

    def test_no_logprobs_is_protocol_unsupported(self, caller: EndpointCaller, fakes: Any) -> None:
        client = OpenAIScoringClient(caller, JEV, "JEV-9B-decision")
        with pytest.raises(ProtocolUnsupported):
            client._top({"choices": [{"text": "true"}]})

    def test_context_overflow_is_one_request(self, caller: EndpointCaller, fakes: Any) -> None:
        millm, _ = fakes
        with pytest.raises(ContextOverflow):
            OpenAIScoringClient(caller, JEV, "JEV-9B-decision").score(row("OVERFLOW " * 3))
        assert len(millm.calls("/v1/completions")) == 1


def test_chat_scoring_variant(caller: EndpointCaller, fakes: Any) -> None:
    millm, _ = fakes
    chat = JEV.model_copy(update={"variant": "chat"})
    millm.p_true = {"pun": 0.9}
    result = OpenAIScoringClient(caller, chat, "JEV-9B-decision").score(row("a pun"))
    (sent,) = millm.calls("/v1/chat/completions")
    assert sent.body["logprobs"] is True and sent.body["top_logprobs"] == 2
    assert sent.body["allowed_token_ids"] == [3721, 1802]
    assert result.distribution["true"] > 0.85


TEI_TEMPLATE = TEIClassificationTemplate(
    kind="tei_classification",
    render="{text}",
    input_fields=["text"],
    label_set=["benign", "injection"],
    positive_class="injection",
    label_map={"SAFE": "benign", "INJECTION": "injection"},
    bound_model_id="protectai/deberta-v3-base-prompt-injection-v2",
)


class TestTEI:
    def test_predict_with_truncate_false_and_label_map(
        self, tei_caller: EndpointCaller, fakes: Any
    ) -> None:
        _, tei = fakes
        result = TEIClassifierClient(tei_caller, TEI_TEMPLATE, tei.model_id).score(row("hello"))
        (sent,) = [r for r in tei.requests if r.path == "/predict"]
        assert sent.body == {"inputs": "hello", "raw_scores": False, "truncate": False}
        assert "x-millm-strict" not in sent.headers
        assert set(result.distribution) == {"benign", "injection"}
        assert math.isclose(sum(result.distribution.values()), 1.0)
        assert len(result.raw_output["scores"]) == 2

    def test_identity_from_info(self, tei_caller: EndpointCaller, fakes: Any) -> None:
        _, tei = fakes
        ident = read_identity(tei_caller)
        assert (ident.model_id, ident.model_sha) == (tei.model_id, tei.model_sha)

    def test_over_length_is_context_overflow(self, tei_caller: EndpointCaller, fakes: Any) -> None:
        with pytest.raises(ContextOverflow):
            TEIClassifierClient(tei_caller, TEI_TEMPLATE, "m").score(row("OVERFLOW"))


class TestBinding:
    def test_a_template_bound_to_model_a_refuses_model_b(self) -> None:
        with pytest.raises(factory.TemplateModelMismatch) as exc:
            factory.check_binding(JEV, "Qwen2.5-7B")
        assert exc.value.code == "TEMPLATE_MODEL_MISMATCH"
        assert "JEV-9B-decision" in str(exc.value) and "Qwen2.5-7B" in str(exc.value)

    def test_build_classifier_checks_the_binding(self, caller: EndpointCaller) -> None:
        with pytest.raises(factory.TemplateModelMismatch):
            factory.build_classifier(caller, "openai_scoring", JEV, "Qwen2.5-7B")
        assert isinstance(
            factory.build_classifier(caller, "openai_scoring", JEV, "JEV-9B-decision"),
            ClassifierClient,
        )


class TestParsers:
    def test_verdict_line(self) -> None:
        assert verdict_line_v1("reasoning\nVERDICT: yes", ["yes", "no"]).verdict == "yes"
        for bad in ("", "VERDICT: maybe", "yes", "VERDICT: yes\nmore"):
            with pytest.raises(ParseFailure):
                verdict_line_v1(bad, ["yes", "no"])

    def test_json(self) -> None:
        schema = {
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string"}},
        }
        parsed = json_v1('{"verdict": "A", "score": 3, "rationale": "r"}', ["A", "B"], schema)
        assert (parsed.verdict, parsed.score, parsed.rationale) == ("A", 3.0, "r")
        for bad in ("", "{", "[]", '{"verdict": "C"}', '{"score": 1}'):
            with pytest.raises(ParseFailure):
                json_v1(bad, ["A", "B"], schema)


PAIRWISE = RubricBody(
    style="pairwise",
    messages=[{"role": "user", "content": "Which is funnier? A: {a} B: {b}"}],  # type: ignore[list-item]
    input_fields=["left", "right"],
    parser="verdict_line_v1",
    allowed_verdicts=["A", "B"],
    pair_fields=("left", "right"),
    swap_map={"A": "B", "B": "A"},
)


class TestJudge:
    def test_swap_and_agree_asks_both_orders(self, caller: EndpointCaller, fakes: Any) -> None:
        millm, _ = fakes
        millm.resident = {**millm.resident, "name": "Qwen2.5-7B"}
        millm.judge_answer = lambda msgs: (
            "VERDICT: A" if "A: cat" in msgs[-1]["content"] else "VERDICT: B"
        )
        judge = OpenAIChatJudge(caller, PAIRWISE, "Qwen2.5-7B", sampling={"seed": 7})
        result = judge.judge(RenderedInput("k", {"left": "cat", "right": "dog"}, None))
        calls = millm.calls("/v1/chat/completions")
        assert len(calls) == 2
        assert "A: cat B: dog" in calls[0].body["messages"][0]["content"]
        assert "A: dog B: cat" in calls[1].body["messages"][0]["content"]
        assert calls[0].body["temperature"] == 0.0 and calls[0].body["seed"] == 7
        assert (result.verdict, result.swapped_verdict) == ("A", "A")  # "B" in swap maps back to A
        assert result.seed_echo == '7;scope="request"'

    def test_unparseable_side_is_not_a_verdict(self, caller: EndpointCaller, fakes: Any) -> None:
        millm, _ = fakes
        millm.resident = {**millm.resident, "name": "Qwen2.5-7B"}
        millm.judge_answer = lambda msgs: "no idea"
        result = OpenAIChatJudge(caller, PAIRWISE, "Qwen2.5-7B").judge(
            RenderedInput("k", {"left": "x", "right": "y"}, None)
        )
        assert result.verdict is None and not result.parse_ok

    def test_seed_not_echoed_reads_none(self, caller: EndpointCaller, fakes: Any) -> None:
        millm, _ = fakes
        millm.resident = {**millm.resident, "name": "Qwen2.5-7B"}
        point = PAIRWISE.model_copy(
            update={
                "style": "pointwise",
                "pair_fields": None,
                "swap_map": None,
                "messages": [PAIRWISE.messages[0].model_copy(update={"content": "Rate {left}"})],
            }
        )
        result = OpenAIChatJudge(caller, point, "Qwen2.5-7B").judge(
            RenderedInput("k", {"left": "x"}, None)
        )
        assert result.seed_echo is None

    def test_structured_mode_decided_by_one_probe(self, caller: EndpointCaller, fakes: Any) -> None:
        millm, _ = fakes
        millm.resident = {**millm.resident, "name": "Qwen2.5-7B"}
        schema = {
            "type": "object",
            "required": ["verdict"],
            "properties": {"verdict": {"type": "string"}},
        }
        rubric = RubricBody(
            style="pointwise",
            messages=[{"role": "user", "content": "Rate {text}"}],  # type: ignore[list-item]
            input_fields=["text"],
            parser="json_v1",
            allowed_verdicts=["good", "bad"],
            json_schema=schema,
        )
        millm.judge_answer = lambda msgs: '{"verdict": "good"}'
        assert (
            decide_structured_mode(caller, rubric, "Qwen2.5-7B", {"text": "x"}, None)
            == "json_schema"
        )
        assert (
            millm.calls("/v1/chat/completions")[0].body["response_format"]["type"] == "json_schema"
        )
        millm.honour_response_format = False
        assert (
            decide_structured_mode(caller, rubric, "Qwen2.5-7B", {"text": "x"}, None)
            == "strict_parse"
        )


class _GoodPlugin:
    def score(self, row: RenderedInput) -> Any:
        return None


def test_plugins_load_only_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.clients.labelers import plugins
    from src.operators.plugins import EntryPointInfo

    class EP:
        loaded = 0

        def load(self) -> Any:
            EP.loaded += 1
            return lambda options: _GoodPlugin()

    info = EntryPointInfo("acme-cls", "1.0", "acme", "acme:factory", EP())  # type: ignore[arg-type]
    monkeypatch.setattr(plugins, "list_entry_points", lambda group: [info])
    with pytest.raises(PluginRefused) as exc:
        load_classifier("acme", {}, allowed=lambda: set())
    assert exc.value.code == "plugin_not_allowed" and EP.loaded == 0
    assert isinstance(load_classifier("acme", {}, allowed=lambda: {info.triple}), _GoodPlugin)

    class BadEP(EP):
        def load(self) -> Any:
            return lambda options: object()

    bad = EntryPointInfo("acme-cls", "1.0", "acme", "acme:factory", BadEP())  # type: ignore[arg-type]
    monkeypatch.setattr(plugins, "list_entry_points", lambda group: [bad])
    with pytest.raises(PluginRefused) as exc2:
        load_classifier("acme", {}, allowed=lambda: {bad.triple})
    assert exc2.value.code == "plugin_invalid"
