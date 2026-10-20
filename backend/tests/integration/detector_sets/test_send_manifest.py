"""The send carries the published manifest once miStudio serves the field, and a miStudio ``409`` on
a wrong row count is surfaced per role (009 FTASKS 17.1; FR-009.26, FR-009.28; miStudio 034 FR-26,
XR-1 served since miStudio ``516a2ecb``).

The real send worker and 008's real publish run; only miStudio's HTTP is the fake, whose
``serves_manifest`` toggle changes both the served OpenAPI document and the registration check
(row count before the mapping, miStudio's own ``409`` detail string).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Publish
from src.models.detector_send import MiStudioRegistration
from tests.support.send_fixtures import SendDriver, ready_set, start_send

REGISTER = "/api/v1/probe-monitors/datasets"

#: Each role of the humor fixture set -> (the version it reads, the split it registers).
ROLES = {
    "train": ("train", "train"),
    "id_test": ("train", "test"),
    "ood_eval": ("ood", "test"),
    "calibration_negatives": ("cal", "train"),
}


def published_manifests(send_id: str) -> dict[str, dict[str, Any]]:
    """version id -> the manifest 008 recorded for this send's publish of it."""
    with sync_session_factory()() as db:
        rows = db.query(Publish).filter(Publish.send_id == send_id).all()
        return {str(p.version_id): dict(p.published_manifest) for p in rows}


def manifest_rows(manifest: dict[str, Any], split: str) -> int:
    [rows] = [s["rows"] for s in manifest["content"]["splits"] if s["name"] == split]
    return int(rows)


async def test_every_registration_carries_its_versions_published_manifest(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_manifest = True
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error

    manifests = published_manifests(started["send_id"])
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4
    by_split = {(c.body["dataset_id"], c.body["split"]): c.body for c in regs}
    assert len(by_split) == 4
    plan_roles = {r["role"]: r for r in sender.send(started["send_id"]).plan["roles"]}
    for role, (version_key, split) in ROLES.items():
        version_id = versions[version_key]
        [body] = [
            c.body
            for c in regs
            if c.body["split"] == split and c.body["name"] == plan_roles[role]["body"]["name"]
        ]
        # the manifest sent is exactly the one 008 published for THIS role's version
        assert body["dataset_version_manifest"] == manifests[version_id]
        assert body["dataset_version_manifest"]["publication"]["repo_id"] == repos[version_id]
        assert manifest_rows(body["dataset_version_manifest"], split) > 0
    # miStudio verified each one
    assert all(v["dataset_version"]["kind"] for v in sender.mistudio.views.values())


async def test_the_manifest_is_never_sent_while_miStudio_does_not_serve_it(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    assert sender.mistudio.serves_manifest is False
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4
    assert all("dataset_version_manifest" not in c.body for c in regs)


@pytest.mark.parametrize("role", sorted(ROLES))
async def test_a_mistudio_409_on_a_wrong_count_fails_that_role_by_name(
    role: str,
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
) -> None:
    sender.mistudio.serves_manifest = True
    set_id, versions, repos = await ready_set(client, sender)
    version_key, split = ROLES[role]
    version_id = versions[version_key]
    key = (repos[version_id], split)
    true_rows = sum(sender.mistudio.values[key].values())
    # miStudio holds a stale or different split for this role only
    sender.mistudio.downloaded_rows[key] = true_rows + 3

    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    role_id = next(r["role_id"] for r in send.plan["roles"] if r["role"] == role)
    stated = manifest_rows(published_manifests(send.id)[version_id], split)
    assert stated == true_rows  # the fixture's fake agrees with the real manifest
    verbatim = (
        f"split {split!r}: manifest states {stated} rows, downloaded split has {true_rows + 3}"
    )

    assert send.state == "failed"
    assert send.error["code"] == "manifest_refused"
    # FR-009.28: the failed step and the role, named
    assert send.error["step"] == "register"
    assert send.error["unit"] == role_id and send.error["role_ids"] == [role_id]
    assert send.error["details"]["role"] == role
    assert send.error["details"]["split"] == split
    assert send.error["details"]["status"] == 409
    assert send.error["details"]["detail"] == verbatim
    assert f"role {role}" in send.error["message"] and verbatim in send.error["message"]
    assert send.error["next_step"]

    steps = sender.steps(send.id)
    failed = steps[("register", role_id)]
    assert failed.state == "failed" and failed.error["code"] == "manifest_refused"
    assert failed.probe_dataset_id is None
    # every other role is either registered or not yet attempted; none failed
    others = [s for (kind, unit), s in steps.items() if kind == "register" and unit != role_id]
    assert len(others) == 3
    assert {s.state for s in others} <= {"done", "pending"}
    # exactly one registration was sent for the role, carrying its version's manifest
    name = next(r["body"]["name"] for r in send.plan["roles"] if r["role"] == role)
    sent = [c for c in sender.mistudio.calls("POST", REGISTER) if c.body["name"] == name]
    assert len(sent) == 1
    assert sent[0].body["dataset_version_manifest"]["publication"]["repo_id"] == key[0]
    assert sender.job(started["job_id"]).status == "failed"


# --- Registration reuse must not outlive the manifest (live finding, 2026-10-20) ----------------


async def resend(client: httpx.AsyncClient, sender: SendDriver, set_id: str) -> str:
    started = await start_send(client, set_id)
    sender.run(started["job_id"])
    return str(started["send_id"])


def register_steps(sender: SendDriver, send_id: str) -> dict[str, Any]:
    return {unit: s for (kind, unit), s in sender.steps(send_id).items() if kind == "register"}


async def test_a_resend_registers_afresh_when_the_reused_view_has_no_manifest(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """Live 009 17.1 (2026-10-20, ``dsn_ffe4a211``): a set sent before miStudio served the manifest
    was re-sent after it did; every registration came back ``reused`` -> the old views
    (``dataset_version: null``), no manifest was sent, and the send read ``completed``. Once the
    field is served, a known view registered without this publish's manifest is not reused."""
    set_id, _, _ = await ready_set(client, sender)
    first = await resend(client, sender, set_id)  # miStudio does not serve the field yet
    assert sender.send(first).state == "completed", sender.send(first).error
    old_views = {s.probe_dataset_id for s in register_steps(sender, first).values()}
    assert len(old_views) == 4
    assert all(sender.mistudio.views[v]["dataset_version"] is None for v in old_views)

    sender.mistudio.serves_manifest = True  # miStudio upgraded (XR-1 served)
    sender.mistudio.requests.clear()
    second = await resend(client, sender, set_id)
    send = sender.send(second)
    assert send.state == "completed", send.error

    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4  # one fresh registration per role, no more
    manifests = published_manifests_for_versions()
    plan_roles = {r["role_id"]: r for r in send.plan["roles"]}
    steps = register_steps(sender, second)
    assert {s.state for s in steps.values()} == {"done"}
    for role_id, step in steps.items():
        assert step.probe_dataset_id not in old_views
        view = sender.mistudio.views[step.probe_dataset_id]
        assert view["dataset_version"] is not None  # miStudio verified it
        assert step.request_body["dataset_version_manifest"] == (
            manifests[plan_roles[role_id]["version_id"]]
        )
    # the old views stay traceable (P-21: probe dataset id -> version)
    with sync_session_factory()() as db:
        held = {
            r.probe_dataset_id
            for r in db.query(MiStudioRegistration).filter(MiStudioRegistration.kind == "view")
        }
    assert old_views <= held

    # a third send reuses the manifest-bearing views and registers nothing
    sender.mistudio.requests.clear()
    third = await resend(client, sender, set_id)
    assert sender.send(third).state == "completed", sender.send(third).error
    assert sender.mistudio.calls("POST", REGISTER) == []
    again = register_steps(sender, third)
    assert {s.state for s in again.values()} == {"reused"}
    assert {s.probe_dataset_id for s in again.values()} == {
        s.probe_dataset_id for s in steps.values()
    }


async def test_a_resend_while_the_field_is_not_served_still_reuses(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """The other side of the rule: without the served field there is no manifest to gain, so a
    known view is reused exactly as before."""
    set_id, _, _ = await ready_set(client, sender)
    first = await resend(client, sender, set_id)
    assert sender.send(first).state == "completed"
    sender.mistudio.requests.clear()
    second = await resend(client, sender, set_id)
    assert sender.send(second).state == "completed", sender.send(second).error
    assert sender.mistudio.calls("POST", REGISTER) == []
    assert {s.state for s in register_steps(sender, second).values()} == {"reused"}


def published_manifests_for_versions() -> dict[str, dict[str, Any]]:
    """version id -> the manifest of its PUBLISHED publish (one per version in these tests)."""
    with sync_session_factory()() as db:
        rows = db.query(Publish).filter(Publish.published_manifest.isnot(None)).all()
        out: dict[str, dict[str, Any]] = {}
        for p in rows:
            out.setdefault(str(p.version_id), dict(p.published_manifest))
        return out


async def test_a_served_field_without_a_manifest_to_send_fails_the_role(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never ``completed`` over a manifest-less registration while miStudio serves the field: a
    role whose publish yields no manifest fails as ``manifest_missing`` and registers nothing."""
    from src.services.publishing import publish_service

    sender.mistudio.serves_manifest = True
    set_id, _, _ = await ready_set(client, sender)
    monkeypatch.setattr(publish_service, "published_manifest_for", lambda db, publish_id: None)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "failed"
    send = sender.send(started["send_id"])
    assert send.error["code"] == "manifest_missing"
    assert send.error["step"] == "register"
    assert send.error["details"]["role_id"] == send.error["unit"]
    assert sender.mistudio.calls("POST", REGISTER) == []
