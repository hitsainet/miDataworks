"""A classifier label run on TypeSafe's JEV API through the real engine (FR-005.56; D58).

The role is configured the way the operator stored it on 2026-10-18: base URL
``https://api.typesafe.ai/v1/systemone``, no model (so ``jev-latest``), the key stored encrypted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from src.clients import endpoint_caller
from src.core.database import sync_session_factory
from src.models.label import Label
from tests.integration.labeling.helpers import texts
from tests.support.fake_millm import ORIGIN as MILLM_ORIGIN
from tests.support.fake_millm import TEI_ORIGIN, route
from tests.support.fake_typesafe import KEY, ORIGIN, FakeTypeSafe
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

TEMPLATE = {
    "name": "typesafe/humor-choice",
    "body": {
        "kind": "typesafe_systemone",
        "render": "{text}",
        "input_fields": ["text"],
        "instructions": "{question}",
        "criteria": {"humorous": "Most readers would find it funny.", "not_humorous": None},
        "label_set": ["humorous", "not_humorous"],
        "positive_class": "humorous",
    },
}


@pytest.fixture
def typesafe(labeling: Labeling) -> Iterator[FakeTypeSafe]:
    fake = FakeTypeSafe()
    previous = endpoint_caller.install_transport(
        route({MILLM_ORIGIN: labeling.millm, TEI_ORIGIN: labeling.tei, ORIGIN: fake})
    )
    set_operator()
    set_role(
        "classifier",
        base_url=ORIGIN + "/v1/systemone",
        model=None,  # type: ignore[arg-type]
        protocol="typesafe_systemone",
        api_key=KEY,
    )
    yield fake
    endpoint_caller.install_transport(previous)


async def start(client: httpx.AsyncClient, labeling: Labeling, rows: list[str]) -> dict[str, Any]:
    created = (await client.post("/api/v1/decision-templates", json=TEMPLATE)).json()
    version_id = make_version(labeling.data_dir, rows)
    response = await client.post(
        "/api/v1/label-runs",
        json={
            "input_version_id": version_id,
            "role": "classifier",
            "template_id": created["id"],
            "question": "Would most readers find this text funny?",
            "field_map": {"text": "text"},
            "threshold_positive": 0.5,
            "threshold_negative": 0.2,
            "positive_label": "humorous",
            "negative_label": "not_humorous",
        },
    )
    assert response.status_code in (200, 201, 202), response.text
    return dict(response.json())


async def test_a_run_labels_through_typesafe_and_records_each_answer(
    client: httpx.AsyncClient, labeling: Labeling, typesafe: FakeTypeSafe
) -> None:
    rows = [f"a joke number {i}" for i in range(6)] + texts(6)
    run = await start(client, labeling, rows)
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed", final.error
    assert final.endpoint_snapshot["model_id"] == "jev-latest"
    assert final.counts == {"positive": 6, "negative": 6}
    with sync_session_factory()() as db:
        labels = list(db.execute(select(Label).where(Label.label_run_id == run["id"])).scalars())
    assert len(labels) == 12
    one = labels[0]
    assert one.raw_output["model"] == "jev-1.13.0" and one.raw_output["confidence"] == 0.6
    assert one.raw_output["usage"]["output_tokens"] == 34 and one.raw_output["request_id"]
    scored = [r for r in typesafe.requests if r.path == "/v1/systemone"]
    assert len(scored) == 12
    assert {r.path for r in typesafe.requests} == {"/v1/systemone"}  # no miLLM or TEI probe
    assert all(r.body["model"] == "jev-latest" for r in scored)
    assert not [h for r in typesafe.requests for h in r.headers if h.startswith("x-millm")]
    assert labeling.millm.requests == [] and labeling.tei.requests == []
    assert KEY not in str(final.endpoint_snapshot) and KEY not in str(one.raw_output)


async def test_a_version_change_mid_run_stops_the_run(
    client: httpx.AsyncClient, labeling: Labeling, typesafe: FakeTypeSafe
) -> None:
    typesafe.switch_after = 3
    run = await start(client, labeling, texts(10))
    final = labeling.run_until_done(run["id"])
    assert final.state != "completed"
    assert (final.error or {}).get("code") == "MODEL_CHANGED", final.error


async def test_the_role_test_recognises_typesafe(
    client: httpx.AsyncClient, labeling: Labeling, typesafe: FakeTypeSafe
) -> None:
    result = (await client.post("/api/v1/endpoint-roles/classifier/test")).json()
    assert result["server_kind"] == "typesafe" and result["protocol_ok"] is True
    assert result["model_listed"] is True and result["error_code"] is None
    paths = [r.path for r in typesafe.requests]
    assert paths == ["/v1/models", "/v1/systemone"]  # never miLLM's or TEI's routes
