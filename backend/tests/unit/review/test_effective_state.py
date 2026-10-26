"""The effective-label state machine (006 FTASKS 8.3; FTDD 006 section 6.4)."""

from __future__ import annotations

from src.services.review.decision_rules import effective_state


def d(
    i: int, decision: str, origin: str = "operator", label: str | None = None
) -> dict[str, object]:
    return {
        "id": f"d{i}",
        "decision": decision,
        "decided_by_origin": origin,
        "override_label": label,
    }


def test_no_history_is_the_model_label() -> None:
    s = effective_state([], "humorous")
    assert (s.label, s.state, s.decision_id) == ("humorous", "model", None)


def test_an_operator_override_wins() -> None:
    s = effective_state([d(1, "override", label="not_humorous")], "humorous")
    assert (s.label, s.state, s.decision_id) == ("not_humorous", "overridden", "d1")


def test_a_later_operator_accept_restores_the_model_label() -> None:
    s = effective_state([d(1, "override", label="not_humorous"), d(2, "accept")], "humorous")
    assert (s.label, s.state, s.decision_id) == ("humorous", "model", "d2")


def test_an_agent_accept_changes_nothing() -> None:
    s = effective_state(
        [d(1, "override", label="not_humorous"), d(2, "accept", "agent")], "humorous"
    )
    assert (s.label, s.state) == ("not_humorous", "overridden")


def test_any_flag_leaves_the_row_flagged_until_an_operator_decides() -> None:
    assert effective_state([d(1, "flag", "agent")], "h").state == "flagged_unresolved"
    assert (
        effective_state([d(1, "flag"), d(2, "accept", "agent")], "h").state == "flagged_unresolved"
    )
    assert effective_state([d(1, "flag"), d(2, "accept")], "h").state == "model"


def test_an_agent_override_cannot_win_even_if_stored() -> None:
    s = effective_state([d(1, "override", "agent", "not_humorous")], "humorous")
    assert (s.label, s.state) == ("humorous", "model")


def test_operator_reject_is_rejected() -> None:
    assert effective_state([d(1, "reject")], None).state == "rejected"
    assert effective_state([d(1, "reject", "agent")], None).state == "model"
