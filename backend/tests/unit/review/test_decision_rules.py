"""Decision rules for every (decision, origin, queue kind) combination (006 FTASKS 8.2, 8.3)."""

from __future__ import annotations

import itertools

import pytest

from src.services.review import decision_rules as r


@pytest.mark.parametrize(
    ("decision", "origin", "kind"),
    list(itertools.product(r.DECISIONS, ("operator", "agent"), r.QUEUE_KINDS)),
)
def test_allowed_for_every_combination(decision: str, origin: str, kind: str) -> None:
    refused_reject = decision == "reject" and kind != "external"
    refused_agent = origin == "agent" and decision not in ("accept", "flag")
    if refused_reject:
        with pytest.raises(r.DecisionRefused) as info:
            r.allowed(decision, origin, kind)
        assert (info.value.code, info.value.status) == ("DECISION_INVALID", 422)
    elif refused_agent:
        with pytest.raises(r.DecisionRefused) as info:
            r.allowed(decision, origin, kind)
        assert (info.value.code, info.value.status) == ("AGENT_DECISION_NOT_ALLOWED", 403)
    else:
        r.allowed(decision, origin, kind)


def test_unknown_decision_refused() -> None:
    with pytest.raises(r.DecisionRefused):
        r.allowed("approve", "operator", "label_review")


LABELS = ["humorous", "not_humorous"]


def _validate(decision: str, **kw: object) -> str:
    args: dict[str, object] = {
        "override_label": None,
        "reason": None,
        "label_set": LABELS,
        "queue_kind": "label_review",
        "model_output_visible": True,
    }
    args.update(kw)
    return r.validate(decision, **args)  # type: ignore[arg-type]


def test_accept_gets_the_default_reason() -> None:
    assert _validate("accept") == r.DEFAULT_ACCEPT_REASON
    assert _validate("accept", reason=" looks right ") == "looks right"


@pytest.mark.parametrize("decision", ["override", "flag", "reject"])
def test_reason_required(decision: str) -> None:
    kw = {"override_label": "humorous"} if decision == "override" else {}
    with pytest.raises(r.DecisionRefused) as info:
        _validate(decision, reason="  ", **kw)
    assert info.value.code == "DECISION_INVALID" and info.value.details == {"field": "reason"}


def test_override_label_must_be_in_the_label_set() -> None:
    with pytest.raises(r.DecisionRefused) as info:
        _validate("override", override_label="sarcastic", reason="x")
    assert info.value.details["allowed_labels"] == LABELS
    with pytest.raises(r.DecisionRefused):
        _validate("override", reason="x")


def test_only_an_override_carries_a_label() -> None:
    with pytest.raises(r.DecisionRefused):
        _validate("flag", override_label="humorous", reason="x")


def test_calibration_labeling_assigns_directly_and_cannot_accept_hidden_output() -> None:
    assert (
        _validate(
            "override",
            override_label="humorous",
            queue_kind="calibration_labeling",
            model_output_visible=False,
        )
        == r.DEFAULT_ASSIGN_REASON
    )
    with pytest.raises(r.DecisionRefused):
        _validate("accept", queue_kind="calibration_labeling", model_output_visible=False)
    assert _validate("accept", queue_kind="calibration_labeling", model_output_visible=True)
