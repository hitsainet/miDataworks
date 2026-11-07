"""The behaviour key and positive meaning reach miStudio when it serves both (015 FR-015.2 - 015.6;
XR-5; ENH-005 D37).

The real send worker and 008's real publish run; only miStudio's HTTP is the fake, whose
``serves_behaviour`` toggle changes the served OpenAPI document and the registration check
(``extra="forbid"`` refuses the keys while they are not served).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from src.core.database import sync_session_factory
from src.models.detector_send import MiStudioRegistration
from tests.support.send_fixtures import SendDriver, ready_set, start_send

REGISTER = "/api/v1/probe-monitors/datasets"
MEANING = "Most readers would find the text funny"
KEYED = {"behaviour_key": "humor"}


async def test_a_served_capability_sends_both_keys_once_per_role(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    sender.mistudio.serves_behaviour = True
    set_id, _, _ = await ready_set(client, sender, body_over=KEYED)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed", sender.send(
        started["send_id"]
    ).error
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4
    for call in regs:
        assert call.body["behaviour"] == "humor"
        assert call.body["positive_meaning"] == MEANING
    assert {v["behaviour"] for v in sender.mistudio.views.values()} == {"humor"}
    steps = [s for s in sender.steps(started["send_id"]).values() if s.step == "register"]
    assert len(steps) == 4 and all(s.behaviour == {"sent": True, "reason": "sent"} for s in steps)
    with sync_session_factory()() as db:
        hashes = {
            r.meaning_sha256 for r in db.query(MiStudioRegistration).all() if r.kind == "view"
        }
    assert len(hashes) == 1 and None not in hashes


async def test_an_unserved_capability_sends_neither_and_says_why(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    assert sender.mistudio.serves_behaviour is False
    set_id, _, _ = await ready_set(client, sender, body_over=KEYED)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 4
    assert not any({"behaviour", "positive_meaning"} & set(c.body) for c in regs)
    steps = [s for s in sender.steps(started["send_id"]).values() if s.step == "register"]
    assert len(steps) == 4
    for step in steps:
        assert step.behaviour is not None and step.behaviour["sent"] is False
        assert "not served by miStudio" in step.behaviour["reason"]


async def test_a_half_served_pair_is_not_served(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """T6: one property alone never sends a behaviour without its meaning."""
    sender.mistudio.serves_behaviour_only = True
    set_id, _, _ = await ready_set(client, sender, body_over=KEYED)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    assert not any("behaviour" in c.body for c in sender.mistudio.calls("POST", REGISTER))


async def test_the_approval_digest_binds_the_key(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """FR-015.2: changing only the behaviour key changes the snapshot and the digest."""
    from src.services.detector_sets import send_service
    from src.services.detector_sets.set_service import Who

    set_id, _, _ = await ready_set(client, sender, body_over=KEYED)

    def prepared() -> tuple[str, str]:
        with sync_session_factory()() as db:
            p = send_service.prepare(
                db,
                set_id,
                explicit_repos={},
                namespace="dw-test",
                visibility="private",
                who=Who("Test Operator", "operator"),
            )
            return p.snapshot_sha256, p.digest

    before = prepared()
    patched = await client.patch(
        f"/api/v1/detector-sets/{set_id}", json={"behaviour_key": "humour"}
    )
    assert patched.status_code == 200 and patched.json()["behaviour_key"] == "humour"
    after = prepared()
    assert before[0] != after[0] and before[1] != after[1]


async def test_a_view_registered_without_the_meaning_is_not_reused_once_served(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """FR-015.6: an older view (meaning hash NULL) is not reused when miStudio serves XR-5."""
    set_id, _, _ = await ready_set(client, sender, body_over=KEYED)
    first = await start_send(client, set_id)
    assert sender.run(first["job_id"])["status"] == "completed"
    assert len(sender.mistudio.calls("POST", REGISTER)) == 4

    sender.mistudio.serves_behaviour = True
    second = await start_send(client, set_id)
    assert sender.run(second["job_id"])["status"] == "completed", sender.send(
        second["send_id"]
    ).error
    regs = sender.mistudio.calls("POST", REGISTER)
    assert len(regs) == 8, "every view re-registered with the meaning"
    assert all(c.body.get("behaviour") == "humor" for c in regs[4:])
    # A third send reuses the views that carry the meaning.
    third = await start_send(client, set_id)
    assert sender.run(third["job_id"])["status"] == "completed"
    assert len(sender.mistudio.calls("POST", REGISTER)) == 8
