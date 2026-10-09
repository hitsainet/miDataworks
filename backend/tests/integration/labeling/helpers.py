"""Small helpers shared by feature 005 integration tests."""

from __future__ import annotations

from typing import Any

import httpx

from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

QUESTION = "Is this text intended to be humorous?"
AGENT = {"X-Dataworks-Agent": "agent:claude"}


async def jev_template_id(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/decision-templates")
    assert response.status_code == 200, response.text
    (jev,) = [t for t in response.json() if t["name"] == "jev/noul-bare-v1"]
    return str(jev["id"])


def texts(n: int, prefix: str = "row") -> list[str]:
    return [f"{prefix} {i:06d}" for i in range(n)]


async def setup_classifier(
    client: httpx.AsyncClient, lab: Labeling, n: int = 30, **version_kw: Any
) -> tuple[str, str]:
    set_operator()
    set_role("classifier")
    version_id = make_version(lab.data_dir, version_kw.pop("rows", None) or texts(n), **version_kw)
    return version_id, await jev_template_id(client)


def start_body(version_id: str, template_id: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "input_version_id": version_id,
        "role": "classifier",
        "template_id": template_id,
        "question": QUESTION,
        "field_map": {"text": "text"},
        "threshold_positive": 0.5,
        "threshold_negative": 0.2,
        "positive_label": "funny",
        "negative_label": "not funny",
    }
    body.update(overrides)
    return body
