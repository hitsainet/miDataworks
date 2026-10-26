"""Previews on a paid classifier (Review R4 of 017 slices 5 - 7; FR-017.40, NFR "Cost").

"Try it on a sample" (``POST /labeling/sample``, ``dataworks_label_sample``) and the keep-share
estimate (``POST /labeling/keep-share``) send rows' text to the classifier role like a run does.
On a PAID role an agent's preview is refused before anything is sent (the run, which waits for the
operator, is the way to score on it), and anyone's preview is refused as a run would be: unpriced,
no ceiling, or an estimate past the ceiling. TypeSafe is the fake; nothing paid is called.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import func, select

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models import Job
from src.models.approval import Approval
from tests.integration.labeling.helpers import texts
from tests.support.labeling_fixtures import set_role
from tests.support.second_opinion import AGENT, Paid, paid, template_id, version

__all__ = ["paid"]


def sample_body(v: str, t: str, rows: int = 3) -> dict[str, object]:
    return {
        "input_version_id": v,
        "role": "classifier",
        "template_id": t,
        "question": "Does the text show the behaviour?",
        "field_map": {"text": "text"},
        "rows": rows,
    }


def keep_share_body(v: str, t: str) -> dict[str, object]:
    return {
        "input_version_id": v,
        "template_id": t,
        "question": "Does the text show the behaviour?",
        "field_map": {"text": "text"},
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
        "sample_rows": 5,
    }


def jobs() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(Job)).scalar_one())


def approvals() -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(Approval)).scalar_one())


@pytest.mark.parametrize("route", ["sample", "keep-share"])
async def test_an_agent_preview_on_a_paid_classifier_sends_nothing(
    client: httpx.AsyncClient, paid: Paid, route: str
) -> None:
    v = version(paid.lab, texts(8))
    t = await template_id(client)
    payload = sample_body(v, t) if route == "sample" else keep_share_body(v, t)
    before = jobs()
    response = await client.post(f"/api/v1/labeling/{route}", json=payload, headers=AGENT)
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "paid_classifier_preview_refused", error
    assert error["details"]["next"]["tool"] == "dataworks_plan_label_run"
    assert paid.scoring() == [], "no row text reaches the paid endpoint"
    assert jobs() == before and approvals() == 0


@pytest.mark.parametrize("route", ["sample", "keep-share"])
@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("unpriced", "classifier_unpriced"),
        ("no_ceiling", "classifier_spend_ceiling"),
        ("past_ceiling", "classifier_spend_ceiling"),
    ],
)
async def test_an_operator_preview_on_a_paid_classifier_obeys_the_price_and_ceiling(
    client: httpx.AsyncClient,
    paid: Paid,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    change: str,
    code: str,
) -> None:
    if change == "unpriced":
        paid.priced(None)
    elif change == "no_ceiling":
        monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", None)
    else:
        monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", 0.0001)
    v = version(paid.lab, texts(8))
    t = await template_id(client)
    payload = sample_body(v, t) if route == "sample" else keep_share_body(v, t)
    before = jobs()
    response = await client.post(f"/api/v1/labeling/{route}", json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert paid.scoring() == [] and jobs() == before


async def test_an_operator_sample_on_a_priced_classifier_within_the_ceiling_runs(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    v = version(paid.lab, texts(8))
    t = await template_id(client)
    response = await client.post("/api/v1/labeling/sample", json=sample_body(v, t))
    assert response.status_code == 200, response.text
    assert len(paid.scoring()) == 3


async def test_an_agent_preview_on_a_local_classifier_is_unchanged(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    from tests.integration.labeling.helpers import jev_template_id

    set_role("classifier")  # the fake miLLM, no price: not paid
    v = version(paid.lab, texts(8))
    t = await jev_template_id(client)
    response = await client.post("/api/v1/labeling/sample", json=sample_body(v, t), headers=AGENT)
    assert response.status_code == 200, response.text
