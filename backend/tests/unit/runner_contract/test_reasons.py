"""The reason vocabulary (FR-018.7; FTASKS 3.1)."""

from __future__ import annotations

from src.runner_contract.reasons import HOST_FAULTS, REASONS, Reason


def test_reasons_tuple_is_the_enum_values_in_order() -> None:
    assert REASONS == tuple(r.value for r in Reason)


def test_there_are_exactly_eight_reasons() -> None:
    assert len(REASONS) == 8
    assert len(set(REASONS)) == 8


def test_host_faults_are_a_subset_that_never_fail_a_step() -> None:
    assert HOST_FAULTS <= set(REASONS)
    assert HOST_FAULTS == {Reason.RULE_WORKER_UNAVAILABLE, Reason.RULE_TIMEOUT}


def test_every_reason_is_in_the_published_contract() -> None:
    import json

    from src.runner_contract.document import PACKAGED_CONTRACT_PATH

    contract = json.loads(PACKAGED_CONTRACT_PATH.read_text(encoding="utf-8"))
    assert set(REASONS) == set(contract["reasons"])
    # reason_meanings describes exactly the reasons.
    assert set(contract["reason_meanings"]) == set(REASONS)
