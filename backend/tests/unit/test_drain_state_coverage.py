"""Every state of the work a cutover must drain is classified (feature 020; FR-020.28; TPD-16;
FTASKS 7.4).

Before each cutover window (P2, P3) the drain reading is ``dataworks_list_jobs(state="active")`` and
the pending approvals. That reading is complete only if every state of every long-running kind is
either visible there, terminal, or resumable after the window. A state added to a model without a
class here turns this red (control M35), so the drain gate can never miss work silently."""

from __future__ import annotations

from src.models import detector_send, generation, label_run, minimal_pair_chain

VISIBLE_JOBS = "visible_jobs"  # a dw_jobs row the state=active listing returns
VISIBLE_APPROVALS = "visible_approvals"  # a pending approval the listing returns
RESUMABLE = "terminal_resumable"  # listed in the window record; resumed afterwards
TERMINAL = "terminal"
NOT_STARTED = "not_started"  # holds no job and calls no sibling until started

CLASSIFIED: dict[tuple[str, str], str] = {
    ("detector_send", "awaiting_approval"): VISIBLE_APPROVALS,
    ("detector_send", "queued"): VISIBLE_JOBS,
    ("detector_send", "running"): VISIBLE_JOBS,
    ("detector_send", "completed"): TERMINAL,
    ("detector_send", "failed"): RESUMABLE,
    ("detector_send", "cancelled"): RESUMABLE,
    ("detector_send", "rejected"): TERMINAL,
    ("label_run", "awaiting_approval"): VISIBLE_APPROVALS,
    ("label_run", "queued"): VISIBLE_JOBS,
    ("label_run", "running"): VISIBLE_JOBS,
    ("label_run", "cancelled"): RESUMABLE,
    ("label_run", "completed"): TERMINAL,
    ("label_run", "failed"): RESUMABLE,
    ("label_run", "rejected"): TERMINAL,
    ("generation_run", "planned"): NOT_STARTED,
    ("generation_run", "queued"): VISIBLE_JOBS,
    ("generation_run", "running"): VISIBLE_JOBS,
    ("generation_run", "completed"): TERMINAL,
    ("generation_run", "cancelled"): RESUMABLE,
    ("generation_run", "failed"): RESUMABLE,
    ("minimal_pair_chain", "running"): VISIBLE_JOBS,
    ("minimal_pair_chain", "completed"): TERMINAL,
    ("minimal_pair_chain", "failed"): RESUMABLE,
    ("minimal_pair_chain", "cancelled"): RESUMABLE,
}


def _states() -> set[tuple[str, str]]:
    sources = {
        "detector_send": detector_send.SEND_STATES,
        "label_run": label_run.RUN_STATES,
        "generation_run": generation.RUN_STATES,
        "minimal_pair_chain": minimal_pair_chain.CHAIN_STATES,
    }
    return {(kind, state) for kind, states in sources.items() for state in states}


def test_every_state_is_classified() -> None:
    states = _states()
    assert len(states) >= 20, "the state constants were not read"
    assert (
        set(CLASSIFIED) == states
    ), f"unclassified: {sorted(states - set(CLASSIFIED))}; stale: {sorted(set(CLASSIFIED) - states)}"


def test_resumable_classes_match_the_models() -> None:
    resumable = {k for k, v in CLASSIFIED.items() if v == RESUMABLE}
    assert {s for k, s in resumable if k == "label_run"} == set(label_run.RESUMABLE_RUN_STATES)
    assert {s for k, s in resumable if k == "generation_run"} == set(
        generation.RESUMABLE_RUN_STATES
    )
    assert {s for k, s in resumable if k == "minimal_pair_chain"} == set(
        minimal_pair_chain.RESUMABLE_CHAIN_STATES
    )
    active = {
        s for k, s in CLASSIFIED if k == "detector_send" and CLASSIFIED[(k, s)] == VISIBLE_JOBS
    }
    assert active == set(detector_send.ACTIVE_SEND_STATES)
