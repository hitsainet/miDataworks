"""017 slice 6 fixtures: a PAID classifier (TypeSafe's fake, priced, under a ceiling) beside the
local fakes of ``labeling_fixtures``, and the versions a second opinion reads.

The paid role is configured the way the operator stores it: base URL on TypeSafe's origin, the
key bound to it, a price per call (``price``), and ``CLASSIFIER_SPEND_CEILING_USD`` set
(``ceiling``); either may be left out to test the refusals.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tests.support.fake_millm import ORIGIN as MILLM_ORIGIN
from tests.support.fake_millm import TEI_ORIGIN, route
from tests.support.fake_typesafe import KEY, ORIGIN, FakeTypeSafe
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
TEMPLATE = {
    "name": "typesafe/second-opinion",
    "body": {
        "kind": "typesafe_systemone",
        "render": "{text}",
        "input_fields": ["text"],
        "instructions": "{question}",
        "criteria": {"yes": "The behaviour is present.", "no": None},
        # ["yes", "no"] is not JSONB's key order: the stored body comes back reordered
        "label_set": ["yes", "no"],
        "positive_class": "yes",
    },
}


@dataclass
class Paid:
    fake: FakeTypeSafe
    lab: Labeling

    def priced(self, price: str | None = "0.0008") -> None:
        set_role(
            "classifier",
            base_url=ORIGIN + "/v1/systemone",
            model=None,  # type: ignore[arg-type]
            protocol="typesafe_systemone",
            api_key=KEY,
            price=price,
        )

    def scoring(self) -> list[Any]:
        return [r for r in self.fake.requests if r.path == "/v1/systemone"]


@pytest.fixture
def paid(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> Iterator[Paid]:
    from src.clients import endpoint_caller
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "classifier_spend_ceiling_usd", 25.0)
    fake = FakeTypeSafe()
    previous = endpoint_caller.install_transport(
        route({MILLM_ORIGIN: labeling.millm, TEI_ORIGIN: labeling.tei, ORIGIN: fake})
    )
    set_operator()
    state = Paid(fake, labeling)
    state.priced()
    yield state
    endpoint_caller.install_transport(previous)


async def template_id(client: httpx.AsyncClient) -> str:
    created = await client.post("/api/v1/decision-templates", json=TEMPLATE)
    if created.status_code == 201:
        return str(created.json()["id"])
    listed = (await client.get("/api/v1/decision-templates")).json()
    return str(next(t["id"] for t in listed if t["name"] == TEMPLATE["name"]))


def version(lab: Labeling, texts: Sequence[str], **extra: Sequence[Any]) -> str:
    return make_version(lab.data_dir, list(texts), extra=extra or None)


def body(version_id: str, template: str, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "input_version_id": version_id,
        "role": "classifier",
        "template_id": template,
        "question": "Does the text show the behaviour?",
        "field_map": {"text": "text"},
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
        "positive_label": "yes",
        "negative_label": "no",
    }
    out.update(over)
    return out
