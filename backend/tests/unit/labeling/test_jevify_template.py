"""The built-in jevify template (005 FTASKS 12.3; operator decision D56).

Seeded from the D56 spike (``records/jevify_spike_d56.json``: native bfloat16, miLLM ``ce8046b``,
RTX 3090, 50 rows of ``records/jev_reference_inputs.json``) and tested on the spike's recorded
responses: every constant equals the record's, the product's request is the spike's request, and the
product's arithmetic on each recorded response reproduces the recorded probability.

It is bound to miLLM's served name ``jevify-gemma4-e4b``. Until miLLM 7667e3a, the FP16 and the Q8
registrations shared that name, so the binding could not select native; the Q8 row is deleted and
the name is unique (suite tracker 3.7).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.clients.labelers.base import RenderedInput
from src.clients.labelers.factory import TemplateModelMismatch, build_classifier, check_binding
from src.clients.labelers.openai_scoring import OpenAIScoringClient
from src.schemas.labeling import OpenAIScoringTemplate
from src.services.decision_template_service import builtin_template_documents
from tests.support.source_tree import source_only

NAME = "jevify/yes-no-v1"
SERVED = "jevify-gemma4-e4b"
QUESTION = "Would most readers find this text funny?"


@pytest.fixture(scope="module")
def record() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(source_only("records/jevify_spike_d56.json").read_text())
    return data


@pytest.fixture(scope="module")
def inputs() -> dict[str, str]:
    rows = json.loads(source_only("records/jev_reference_inputs.json").read_text())
    return {r["id"]: r["text"] for r in rows}


@pytest.fixture(scope="module")
def template() -> OpenAIScoringTemplate:
    (doc,) = [d for d in builtin_template_documents() if d.name == NAME]
    assert doc.version == 1
    assert isinstance(doc.body, OpenAIScoringTemplate)
    return doc.body


class _NoCalls:
    def call(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("no call is made offline")


def client(template: OpenAIScoringTemplate) -> OpenAIScoringClient:
    return OpenAIScoringClient(_NoCalls(), template, SERVED)  # type: ignore[arg-type]


def test_every_constant_equals_the_spike_record(
    template: OpenAIScoringTemplate, record: dict[str, Any]
) -> None:
    spike = record["template"]
    assert template.render == spike["render"]
    assert template.label_set == spike["label_set"] == ["no", "yes"]
    assert template.positive_class == spike["positive_class"] == "yes"
    assert template.verbalizer_ids == spike["verbalizer_ids"] == [3771, 10784]
    assert {k: list(v) for k, v in template.slots.items()} == spike["slots"]
    assert template.bias == spike["bias"]
    assert template.temperature == spike["temperature"]
    assert template.tokenization == spike["tokenization"] == {"add_special_tokens": False}
    assert template.variant == "completions" and template.kind == "openai_scoring"
    assert template.decision_kind == "noul" and template.input_fields == ["text"]
    assert template.bound_model_revision == record["revision"]
    assert record["model"] == "kushalpatil/jevify-gemma4-e4b"
    # bound to the NATIVE registration's served name (D56: Q8 misses the 0.01 bar)
    assert template.bound_model_id == SERVED
    assert record["system_fingerprint_as_served"]["native_row13"].startswith(f"millm:{SERVED}@")


def test_the_request_is_the_spikes_request(
    template: OpenAIScoringTemplate, inputs: dict[str, str], record: dict[str, Any]
) -> None:
    text = inputs[record["rows"][0]["id"]]
    body = client(template).request_body(
        RenderedInput(row_key="r0", fields={"text": text}, question=QUESTION)
    )
    expected_prompt = record["template"]["render"].replace("{text}", text)
    expected_prompt = expected_prompt.replace("{question}", record["template"]["question"])
    assert body == {
        "model": SERVED,
        "prompt": expected_prompt,
        "max_tokens": 1,
        "temperature": 1.0,
        "logprobs": 2,
        "allowed_token_ids": [3771, 10784],
        "add_special_tokens": False,
        "return_tokens_as_token_ids": True,
    }
    assert body["prompt"].startswith("<bos><|turn>system\n")
    assert body["prompt"].endswith("Answer Yes or No.<turn|>\n<|turn>model\n")


def test_braces_in_a_row_do_not_change_the_template(template: OpenAIScoringTemplate) -> None:
    body = client(template).request_body(
        RenderedInput(row_key="r0", fields={"text": "a {question} b"}, question=QUESTION)
    )
    assert "<state>\na {question} b\n</state>" in body["prompt"]


def _auroc(scores: list[float], labels: list[int]) -> float:
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def test_the_recorded_native_responses_reproduce_the_recorded_probabilities(
    template: OpenAIScoringTemplate, record: dict[str, Any]
) -> None:
    c = client(template)
    got = []
    for row in record["rows"]:
        response = {
            "model": SERVED,
            "choices": [{"logprobs": {"top_logprobs": [row["native"]["top_logprobs"]]}}],
            "usage": {"prompt_tokens": row["native"]["prompt_tokens"]},
        }
        result = c.result_from(response, latency_ms=1, steering_header=None, request_body={})
        assert set(result.distribution) == {"no", "yes"}
        assert result.distribution["yes"] == pytest.approx(row["native"]["p_yes"], abs=1e-12)
        assert result.prompt_tokens == row["native"]["prompt_tokens"]
        got.append(result.distribution["yes"])
    assert len(got) == 50
    labels = [int(r["human_label"]) for r in record["rows"]]
    assert round(_auroc(got, labels), 4) == record["summary"]["native"]["auroc"] == 0.7575
    assert sum(got) / len(got) == pytest.approx(record["summary"]["native"]["mean_p"], abs=1e-12)


def test_the_binding_selects_native_and_refuses_another_model(
    template: OpenAIScoringTemplate,
) -> None:
    check_binding(template, SERVED)
    built = build_classifier(_NoCalls(), "openai_scoring", template, SERVED)  # type: ignore[arg-type]
    assert isinstance(built, OpenAIScoringClient)
    for other in ("JEV-9B-decision", "jevify-gemma4-e4b-q8", None):
        with pytest.raises(TemplateModelMismatch):
            check_binding(template, other)
