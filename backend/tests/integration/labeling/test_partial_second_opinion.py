"""A second opinion that stops part way (017 FTASKS 9.8; FR-017.41; FTID §7.7; TD-017.26).

TypeSafe's fake fails every call after 40 of 100 rows: the run keeps the 40 scores, records
``stop_reason = endpoint_failed``, the agreement reads partial with each stratum's coverage
(counted here from the stored labels and the strata the test wrote, not from the code), and an
agent's resume waits for the operator's approval of the REMAINING 60 rows only. A paid run whose
cap is lowered while it runs stops at a chunk boundary (``cap_reached``) and a resume past the cap
is refused. Controls: M23 (coverage over all rows), the resume decorator removed.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.approval import Approval
from src.models.label import Label
from tests.support.labeling_fixtures import row_key
from tests.support.second_opinion import AGENT, Paid, body, paid, template_id, version

__all__ = ["paid"]

N = 100
GENRES = [f"g{i % 4}" for i in range(N)]
TRUTH = [i % 3 == 0 for i in range(N)]


def rows() -> list[str]:
    # "joke" makes the fake answer yes; every fifth row, so run and truth disagree on some
    return [f"row {i:03d}" + (" joke" if i % 5 == 0 else "") for i in range(N)]


def labelled_keys(run_id: str) -> set[str]:
    with sync_session_factory()() as db:
        return set(db.execute(select(Label.row_key).where(Label.label_run_id == run_id)).scalars())


@pytest.fixture
def failing(paid: Paid, monkeypatch: pytest.MonkeyPatch) -> Paid:
    settings = get_settings()
    monkeypatch.setattr(settings, "label_transient_retries", 0)
    monkeypatch.setattr(settings, "label_max_consecutive_failures", 3)
    paid.fake.fail_after = 40
    return paid


async def test_a_run_failing_part_way_keeps_its_scores_and_a_resume_asks_for_the_rest(
    client: httpx.AsyncClient, failing: Paid
) -> None:
    texts = rows()
    v = version(failing.lab, texts, genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs",
        json=body(v, await template_id(client), target_column="truth", chunk_size=10),
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    final = failing.lab.run_until_done(run_id)
    assert final.state == "failed" and final.stop_reason == "endpoint_failed", final.error
    scored = labelled_keys(run_id)
    assert len(scored) == 40, "the 40 scores are kept"

    # the partial report: every figure over the rows scored so far, coverage per stratum
    answer = (await client.get(f"/api/v1/label-runs/{run_id}/agreement")).json()
    assert answer["partial"] is True and answer["stop_reason"] == "endpoint_failed"
    assert answer["column"] == "truth" and answer["by"] == ["genre"]
    genre_of = {row_key(t): g for t, g in zip(texts, GENRES, strict=True)}
    expected = Counter(genre_of[k] for k in scored)
    cells = {c["stratum"]["genre"]: c for c in answer["strata"]["cells"]}
    assert set(cells) == {"g0", "g1", "g2", "g3"}
    for genre, cell in cells.items():
        assert cell["rows"] == GENRES.count(genre)
        assert cell["scored"] == expected[genre], (genre, cell)
        assert cell["coverage"] == pytest.approx(expected[genre] / GENRES.count(genre))
    assert answer["strata"]["coverage"] == {"rows": N, "scored": 40}

    # an agent's resume waits; the approval names the remaining rows only
    resumed = await client.post(f"/api/v1/label-runs/{run_id}/resume", headers=AGENT)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["action"] == "paid_classifier_run"
    with sync_session_factory()() as db:
        pending = db.get(Approval, resumed.json()["approval_id"])
        assert pending is not None
        left = pending.payload["remainder"]
        assert (left["rows_remaining"], left["rows_scored"], left["rows_total"]) == (60, 40, N)
        assert left["estimated_usd"] == pytest.approx(60 * 0.0008)
        assert "60 of 100 rows" in pending.summary
    assert failing.lab.run(run_id).state == "failed", "nothing resumed before the approval"
    failing.fake.fail_after = None
    approved = await client.post(f"/api/v1/approvals/{resumed.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    done = failing.lab.run_until_done(run_id)
    assert done.state == "completed" and done.stop_reason is None
    assert len(labelled_keys(run_id)) == N
    whole = (await client.get(f"/api/v1/label-runs/{run_id}/agreement")).json()
    assert whole["partial"] is False and whole["strata"]["coverage"] == {"rows": N, "scored": N}


async def test_an_operator_resume_of_an_unpaid_run_needs_no_approval(
    client: httpx.AsyncClient, failing: Paid
) -> None:
    failing.priced("0")  # priced at zero: not paid
    v = version(failing.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    assert failing.lab.run_until_done(run_id).stop_reason == "endpoint_failed"
    failing.fake.fail_after = None
    resumed = await client.post(f"/api/v1/label-runs/{run_id}/resume", headers=AGENT)
    assert resumed.status_code == 200, resumed.text  # unpaid: P-07's rule, no approval


async def test_a_paid_run_stops_at_the_cap_and_a_resume_past_it_is_refused(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    v = version(paid.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    monkeypatch.setattr(get_settings(), "classifier_run_cap_rows", 20)  # lowered while queued
    final = paid.lab.run_until_done(run_id)
    assert final.state == "failed" and final.stop_reason == "cap_reached", final.error
    assert final.error is not None and final.error["code"] == "CLASSIFIER_CAP_REACHED"
    assert len(labelled_keys(run_id)) == 20
    refused = await client.post(f"/api/v1/label-runs/{run_id}/resume")
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "classifier_cap_exceeded"


async def test_a_cancelled_run_records_its_stop_reason(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    v = version(paid.lab, rows()[:10])
    started = await client.post("/api/v1/label-runs", json=body(v, await template_id(client)))
    cancelled = await client.post(f"/api/v1/label-runs/{started.json()['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["stop_reason"] == "cancelled"


async def test_an_approved_paid_resume_whose_price_rose_does_not_run(
    client: httpx.AsyncClient, failing: Paid
) -> None:
    """Review R4: the operator approved the remaining 60 rows at $0.0008 a call; the price rose
    before the approval: ``PLAN_CHANGED`` and the run stays as it was."""
    v = version(failing.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    assert failing.lab.run_until_done(run_id).stop_reason == "endpoint_failed"
    failing.fake.fail_after = None
    resumed = await client.post(f"/api/v1/label-runs/{run_id}/resume", headers=AGENT)
    assert resumed.status_code == 202, resumed.text
    failing.priced("0.001")
    await client.post(f"/api/v1/approvals/{resumed.json()['approval_id']}/approve")
    with sync_session_factory()() as db:
        stored = db.get(Approval, resumed.json()["approval_id"])
        assert stored is not None and stored.status == "failed", stored
        assert (stored.error or {}).get("code") == "PLAN_CHANGED", stored.error
    assert failing.lab.run(run_id).state == "failed" and len(labelled_keys(run_id)) == 40


async def test_a_paid_run_stops_when_the_ceiling_is_lowered_while_it_runs(
    client: httpx.AsyncClient, paid: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R4: the ceiling is checked at every chunk boundary, not only at the start: lowered
    below what the run has spent after its first chunk, the run stops there, rows kept."""
    v = version(paid.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", 0.005)  # 6.25 rows
    final = paid.lab.run_until_done(run_id)
    assert final.state == "failed" and final.stop_reason == "cap_reached", final.error
    assert final.error is not None and final.error["code"] == "CLASSIFIER_SPEND_CEILING"
    assert len(labelled_keys(run_id)) == 10, "stopped at the first chunk boundary"


async def test_a_run_paid_at_its_start_stays_paid_for_its_resume(
    client: httpx.AsyncClient, failing: Paid
) -> None:
    """Review R4: the role's price is set to zero after a paid run stopped part way; an agent's
    resume of that run still waits for the operator (paid at the start stays paid)."""
    v = version(failing.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    assert failing.lab.run_until_done(run_id).stop_reason == "endpoint_failed"
    failing.fake.fail_after = None
    failing.priced("0")
    resumed = await client.post(f"/api/v1/label-runs/{run_id}/resume", headers=AGENT)
    assert resumed.status_code == 202, resumed.text
    assert resumed.json()["action"] == "paid_classifier_run"


async def test_an_agent_resume_the_gate_could_not_read_is_still_refused(
    client: httpx.AsyncClient, failing: Paid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review R4 (survivor R4-resume-route-guard): the gate's predicate fails OPEN on an
    ``AppError`` (a resume that would be refused is not queued). When its read of the remainder
    fails once and the route's read succeeds, the route's own guard refuses the agent's
    un-approved paid resume: nothing resumes."""
    from src.core.errors import AppError
    from src.services import label_run_service

    v = version(failing.lab, rows(), genre=GENRES, truth=TRUTH)
    started = await client.post(
        "/api/v1/label-runs", json=body(v, await template_id(client), chunk_size=10)
    )
    run_id = started.json()["id"]
    assert failing.lab.run_until_done(run_id).stop_reason == "endpoint_failed"
    real = label_run_service.remainder
    calls: list[int] = []

    async def first_read_fails(db: Any, run: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise AppError("transient", code="TRANSIENT", status_code=409)
        return await real(db, run)

    monkeypatch.setattr(label_run_service, "remainder", first_read_fails)
    resumed = await client.post(f"/api/v1/label-runs/{run_id}/resume", headers=AGENT)
    assert resumed.status_code == 409, resumed.text
    assert resumed.json()["error"]["code"] == "APPROVAL_REQUIRED"
    assert failing.lab.run(run_id).state == "failed"
