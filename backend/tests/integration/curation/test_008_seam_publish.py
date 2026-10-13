"""Feature 004 wired into feature 008's seam, end to end (FR-004.34, FR-004.21; P-01, X-02).

008's ``feature_seams`` loads ``src.services.curation.api`` and calls ``evaluate_warnings`` (C-7) and
``check_leakage`` (C-3). Nothing here stubs 004: the real audit and the real leakage check run on a
real build, then 008's real publish worker decides. Only feature 006's audit status (C-5, not built)
is reported complete, so the outcome turns on 004 alone.

- a version whose ``format`` column predicts the label: a PUBLIC push is refused with C-7 amber
  naming the column; the same version publishes PRIVATELY (with the caveat);
- a version whose metadata predicts nothing and whose splits share no rows: a public push succeeds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from src.core.database import sync_session_factory
from src.models import Job
from src.services.publishing import feature_seams
from tests.support.publish_fixtures import (
    REPO,
    PublishDriver,
    completed_build,
    publish_and_run,
    publisher,
    store_token,
)
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver", "publisher"]
WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike".split()


def _rows(shortcut: bool, n: int = 200) -> list[dict[str, Any]]:
    rng = np.random.default_rng(7)
    out = []
    for i in range(n):
        label = i % 2
        fmt = (
            ("joke" if label else "headline")
            if shortcut
            else ("joke" if rng.random() < 0.5 else "headline")
        )
        words = " ".join(rng.choice(WORDS, size=8))
        out.append({"text": f"item {i} {words}", "label": label, "score": 0.5, "note": fmt})
    return out


async def _version(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, shortcut: bool
) -> str:
    source_id = make_source(data_dir, {"train": _rows(shortcut)}, repo_id="org/humor")
    ds = await client.post("/api/v1/datasets", json={"name": "humor", "target_type": "detector"})
    assert ds.status_code == 201, ds.text
    rec = await client.post(
        "/api/v1/recipes",
        json={"name": "humor-r", "body": body(("stub_split", {"held_out": "test", "count": 20}))},
    )
    assert rec.status_code == 201, rec.text
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source_id}],
        },
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


@pytest.fixture
def audit_sample_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """Feature 006 (C-5) is not built; report its audit complete so only 004 decides."""
    monkeypatch.setattr(
        feature_seams,
        "audit_status",
        lambda version_id, *, session: feature_seams.AuditFinding(
            feature_seams.CHECKED, "complete"
        ),
    )


def test_the_seam_finds_004() -> None:
    assert feature_seams.load_owner(feature_seams.CURATION_API) is not None


async def test_a_shortcut_refuses_a_public_push_and_allows_a_private_one(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    audit_sample_complete: None,
) -> None:
    await store_token(client)
    version_id = await _version(client, driver, data_dir, shortcut=True)
    build = await completed_build(client, publisher, version_id, label_column="label")
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "refused" and pub["error"]["code"] == "publish_refused_amber"
    checks = {c["check"]: c for c in pub["error"]["details"]["checks"]}
    assert set(checks) == {"C-7"}, checks
    evidence = checks["C-7"]["evidence"]
    assert not evidence.get("not_checked")
    assert [w["column"] for w in evidence["warnings"]] == ["note"]
    assert evidence["warnings"][0]["figure"] >= 0.9 and evidence["warnings"][0]["level"] == 10
    assert REPO not in publisher.hub.repos
    private = await publish_and_run(
        client, publisher, version_id, build["id"], visibility="private"
    )
    assert private["status"] == "published", private["error"]


async def test_a_clean_audit_lets_a_public_push_through(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    audit_sample_complete: None,
) -> None:
    await store_token(client)
    version_id = await _version(client, driver, data_dir, shortcut=False)
    build = await completed_build(client, publisher, version_id, label_column="label")
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "published", pub["error"]
    assert pub["visibility_after"] == "public"


async def test_leakage_across_the_held_out_split_turns_c3_amber(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    audit_sample_complete: None,
) -> None:
    """A text repeated in train and test is an exact crossing pair: C-3 amber, public refused."""
    rows = _rows(False)
    for i in range(1, len(rows)):
        rows[i]["text"] = rows[0]["text"] if i % 2 == 0 else rows[i]["text"]
    await store_token(client)
    source_id = make_source(data_dir, {"train": rows}, repo_id="org/humor")
    ds = await client.post("/api/v1/datasets", json={"name": "humor", "target_type": "detector"})
    rec = await client.post(
        "/api/v1/recipes",
        json={"name": "r", "body": body(("stub_split", {"held_out": "test", "count": 20}))},
    )
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source_id}],
        },
    )
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        version_id = str(db.get(Job, response.json()["job_id"]).result["version_id"])
    build = await completed_build(client, publisher, version_id, label_column="label")
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    checks = {c["check"]: c for c in pub["error"]["details"]["checks"]}
    assert "C-3" in checks and not checks["C-3"]["evidence"].get("not_checked")
