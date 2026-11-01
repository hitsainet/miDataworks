"""The fake miLLM follows miLLM's ``text`` scoring contract (miLLM 9765c30, T-49 passed).

miLLM now scores a ``text`` input as ONE USER TURN: the same ids, prompt length and scores as
``messages: [{"role": "user", "content": text}]`` (miLLM's own equality test,
``tests/unit/services/test_probe_scoring.py``). The fake must agree, or a miDataworks test written
against it would pin the old refusal. ``text_input_verified=False`` still models an older miLLM,
with miLLM's refusal body.
"""

from __future__ import annotations

import httpx

from tests.support.fake_millm import ORIGIN, FakeMillm, probe_row

HF_ID = "autotrust/JEV-9B-decision"


def _score(fake: FakeMillm, item: dict) -> httpx.Response:
    with httpx.Client(transport=fake.transport(), base_url=ORIGIN) as client:
        return client.post(
            "/api/probes/score",
            json={"probe_ids": ["pr_humor"], "inputs": [item], "return_token_ids": True},
        )


def _fake() -> FakeMillm:
    fake = FakeMillm()
    fake.probes["pr_humor"] = probe_row("pr_humor", hf_id=HF_ID)
    return fake


def test_text_is_scored_as_one_user_turn() -> None:
    fake = _fake()
    as_text = _score(fake, {"text": "a joke about ducks"})
    as_turn = _score(fake, {"messages": [{"role": "user", "content": "a joke about ducks"}]})
    assert as_text.status_code == 200, as_text.text
    t, m = as_text.json()["data"]["results"][0], as_turn.json()["data"]["results"][0]
    assert t["input_kind"] == "text" and m["input_kind"] == "messages"
    assert t["error"] is None and t["verdicts"]
    for key in ("token_ids", "n_tokens", "prompt_tokens", "verdicts"):
        assert t[key] == m[key], key
    assert fake.probe_score_calls == 2


def test_an_older_millm_refuses_text_with_its_own_body() -> None:
    fake = _fake()
    fake.text_input_verified = False
    response = _score(fake, {"text": "a joke about ducks"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "INVALID_PROBE_SCORE_REQUEST"
    assert error["details"]["param"] == "inputs[0].text"
    assert error["details"]["pending_verification"] == "T-49 (027 FTASKS 0.2)"
    assert fake.probe_score_calls == 0
