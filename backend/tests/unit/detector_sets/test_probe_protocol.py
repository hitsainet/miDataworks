"""The probe-verdict protocol's pure parts (009 FTASKS 12.4 as reinterpreted, 12.6; FR-009.47 -
FR-009.51, FR-009.81): one scored input -> one label, the ``>=`` boundary, miLLM's refusals named,
the identity with nothing filled in."""

from __future__ import annotations

from typing import Any

import pytest

from src.clients.endpoint_errors import RowError
from src.clients.labelers.probe_score import ProbeScore
from src.services.detector_sets import probe_protocol as pp
from src.services.detector_sets import reproduction


def verdict(**over: Any) -> dict[str, Any]:
    base = {
        "probe_id": "pr_1",
        "name": "humor",
        "window": "all",
        "score": 3.0,
        "threshold": 2.5,
        "verdict": True,
        "rung": 2,
        "rung_language": "detects on unseen tasks",
        "provisional": False,
        "threshold_revision": 1,
        "n_scored_tokens": 12,
        "not_scored_reason": None,
    }
    base.update(over)
    return base


def scored(v: dict[str, Any] | None = None, *, error: dict[str, Any] | None = None) -> ProbeScore:
    result = {
        "index": 0,
        "input_kind": "messages",
        "n_tokens": 14,
        "prompt_tokens": 14,
        "token_ids": [1, 2, 3],
        "verdicts": [] if v is None else [v],
        "error": error,
    }
    return ProbeScore(
        verdicts=list(result["verdicts"]),
        token_ids=result["token_ids"],
        n_tokens=14,
        model={
            "hf_id": "meta-llama/x",
            "revision": "abc",
            "dtype": "bfloat16",
            "quantization": None,
        },
        skipped=[],
        request_body={},
        result=result,
        error=error,
        latency_ms=5,
    )


def record(v: dict[str, Any], **kw: Any) -> Any:
    return pp.record_for(
        "a" * 64,
        {"text": "Senate passes cheese bill"},
        scored(v),
        probe_id="pr_1",
        window="all",
        threshold_revision=kw.get("revision", 1),
    )


def test_a_score_exactly_on_the_bar_is_positive() -> None:
    out = record(verdict(score=2.5, threshold=2.5, verdict=True))
    assert out.outcome == "positive" and out.provisional is False
    assert out.parsed_value["score"] == 2.5 and out.parsed_value["threshold"] == 2.5
    assert out.probability is None  # a probe score is not a probability


def test_a_verdict_the_bar_does_not_support_stops_the_run() -> None:
    # miLLM deciding with ">" would answer false on the bar; the shared rule says it fires.
    with pytest.raises(pp.ProbeRefused) as caught:
        record(verdict(score=2.5, threshold=2.5, verdict=False))
    assert caught.value.code == "VERDICT_BOUNDARY_DISAGREES"
    with pytest.raises(pp.ProbeRefused):
        record(verdict(score=2.0, threshold=2.5, verdict=True))


def test_below_the_bar_is_negative() -> None:
    assert record(verdict(score=2.4999, threshold=2.5, verdict=False)).outcome == "negative"


def test_a_null_verdict_is_skipped_with_millms_reason_never_negative() -> None:
    out = record(
        verdict(score=None, verdict=None, n_scored_tokens=0, not_scored_reason="no_scored_tokens")
    )
    assert out.outcome == "skipped" and out.skip_reason == "no_scored_tokens"


def test_a_provisional_verdict_is_excluded_and_flagged() -> None:
    out = record(verdict(provisional=True, verdict=True, score=9.0))
    assert out.outcome == "excluded" and out.provisional is True
    assert out.parsed_value["verdict"] is True  # the verdict is kept in the parsed value


def test_every_reported_fact_is_recorded_and_a_missing_one_reads_not_reported() -> None:
    v = verdict()
    del v["rung_language"]
    out = record(v)
    p = out.parsed_value
    assert p["rung_language"] == "not reported"
    assert p["token_ids"] == [1, 2, 3] and p["n_tokens"] == 14 and p["sent_singly"] is True
    assert p["window"] == "all" and p["threshold_revision"] == 1 and p["rung"] == 2
    assert out.steering_state == pp.STEERING_STATE


def test_a_moved_bar_mid_run_stops_the_run() -> None:
    with pytest.raises(pp.ProbeRefused) as caught:
        record(verdict(threshold_revision=2), revision=1)
    assert caught.value.code == "THRESHOLD_CHANGED"


def test_a_per_input_error_is_a_skipped_row() -> None:
    out = pp.record_for(
        "b" * 64,
        {"text": "x"},
        scored(None, error={"code": "TOKENIZATION_FAILED", "message": "rendering failed"}),
        probe_id="pr_1",
        window="all",
        threshold_revision=1,
    )
    assert out.outcome == "skipped" and out.skip_reason == "TOKENIZATION_FAILED"


def test_two_verdicts_for_one_window_is_a_contract_break() -> None:
    two = scored(verdict())
    two.verdicts.append(verdict())
    with pytest.raises(pp.ProbeRefused) as caught:
        pp.the_verdict(two, "pr_1", "all")
    assert caught.value.code == "PROBE_RESPONSE_UNEXPECTED"


@pytest.mark.parametrize(
    ("server_code", "ours"),
    [
        ("probe_not_found", "PROBE_NOT_IMPORTED"),
        ("probe_model_mismatch", "PROBE_IDENTITY_MISMATCH"),
        ("probe_dtype_mismatch", "PROBE_IDENTITY_MISMATCH"),
        ("probe_hook_unsupported", "PROBE_GGUF_UNSUPPORTED"),
        ("probe_no_model_loaded", "MODEL_NOT_LOADED"),
        ("invalid_probe_score_request", "PROBE_REQUEST_REFUSED"),
    ],
)
def test_millms_refusals_are_named_with_its_reason(server_code: str, ours: str) -> None:
    exc = RowError(
        f"409 {server_code}: miLLM's own words",
        409,
        server_code=server_code,
        details={"mismatches": [{"field": "hf_id"}], "user_message": "x"},
    )
    refused = pp.refusal_for(exc)
    assert refused is not None and refused.code == ours
    assert "miLLM's own words" in refused.message
    assert refused.details["millm_code"] == server_code.upper()
    assert refused.details["millm_details"] == {"mismatches": [{"field": "hf_id"}]}


def test_a_row_level_4xx_is_not_a_refusal() -> None:
    assert pp.refusal_for(RowError("422 something", 422, server_code=None)) is None


def _probe(mistudio: str | None) -> pp.ProbeInfo:
    return pp.ProbeInfo(
        probe_id="pr_1",
        name="humor",
        hf_id="meta-llama/x",
        layer=16,
        scope="all",
        threshold=2.5,
        threshold_revision=3,
        window_thresholds={"all": 2.5},
        rung=2,
        rung_language="detects on unseen tasks",
        armed=False,
        mistudio_probe_id=mistudio,
        mistudio_run_id=None,
        load_dtype=None,
    )


def test_the_identity_names_every_fact_and_fills_in_none() -> None:
    ident = pp.identity(
        _probe(None), model_id="Llama", model_revision=None, window="last_user", form="messages"
    )
    assert ident == {
        "protocol": "millm_probe_score",
        "model_id": "Llama",
        "model_revision": "not reported",
        "probe_id": "pr_1",
        "mistudio_probe_id": "not reported",
        "threshold_revision": 3,
        "window": "last_user",
        "input_form": "messages",
        "sent": "one input per request",
    }


def test_a_window_without_its_own_bar_is_provisional() -> None:
    probe = _probe("pm_1")
    assert probe.window_bar("all") == {"threshold": 2.5, "provisional": False}
    assert probe.window_bar("response") == {"threshold": 2.5, "provisional": True}


def test_the_input_form_is_one_column() -> None:
    assert pp.input_form({"text": "body"}) == "one_user_turn"
    assert pp.input_form({"messages": "conversation"}) == "messages"
    with pytest.raises(pp.ProbeRefused):
        pp.input_form({"text": "a", "messages": "b"})


def test_the_gate_names_both_figures_when_it_fails() -> None:
    target = reproduction.Target(
        mistudio_probe_id="pm_1",
        snapshot_id="dres_1",
        set_id="ds_1",
        send_id="snd_1",
        probe_dataset_id="pmd_1",
        role="id_test",
        view_name="humor-test",
        version_id="v",
        split="test",
        input_column="text",
        label_column="label",
        label_mapping={"humorous": "positive", "not_humorous": "negative"},
        auroc=0.975,
        ci=(0.9652, 0.9837),
        n_rows=4,
    )
    failed = reproduction.judge(target, [1.0, 2.0, 3.0, 4.0], [False, False, True, True], 0)
    assert failed["state"] == "failed" and failed["millm_auroc"] == 1.0
    message = reproduction.failure_message(failed)
    assert "1.0000" in message and "0.9750" in message and "[0.9652, 0.9837]" in message
    passed = reproduction.judge(
        target, [1.0, 2.0, 1.5, 4.0, 3.0], [False, False, True, True, True], 1
    )
    # 5 of 6 pairs correct = 0.8333: outside; then one inside the interval
    assert passed["state"] == "failed"
    inside = reproduction.judge(
        reproduction.Target(**{**target.__dict__, "ci": (0.80, 0.90)}),
        [1.0, 2.0, 1.5, 4.0, 3.0],
        [False, False, True, True, True],
        1,
    )
    assert inside["state"] == "passed" and inside["rows_dropped"] == 1
