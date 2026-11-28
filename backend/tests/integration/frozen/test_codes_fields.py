"""DR-18: wire field names and error codes other repositories match on stay (feature 020;
FR-020.2). Each literal is produced by the code path that sends or answers it: the miStudio client
on an ``httpx`` fake transport, the routes, the live registries and the probe protocol's refusal
builder. Control M2 (a renamed client code) and M6 (an approval action dropped) turn this red.

DR-34: the ``"millm"`` extension block miServer returns is read at its three sites through the
real label-run paths (020 FTASKS 2.13).

DR-49 (register, 2026-11-11): the stored snapshot key ``millm_model`` a probe-verdict run writes and
serves, and miServer's stream field ``millm_steering`` this repository reads a steering report from.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.clients import miworkbench_client as mc
from src.clients.endpoint_errors import RowError
from src.core.config import get_settings
from src.core.job_kinds import get_job_kind
from src.services.detector_sets import probe_protocol as pp
from tests.integration.detector_sets import test_feature_tag_runs as features
from tests.integration.detector_sets.test_probe_verdict_runs import LLAMA as PROBE_MODEL
from tests.integration.detector_sets.test_probe_verdict_runs import body as probe_body
from tests.integration.detector_sets.test_probe_verdict_runs import (
    millm,  # noqa: F401 - fixture
    plan,
    refreshed,
    rows,
)
from tests.integration.detector_sets.test_reproduction_links import (  # noqa: F401 - fixture
    gate_texts,
    labelled_version,
    link_body,
    studio,
)
from tests.integration.detector_sets.test_results_and_marks import sent_set
from tests.integration.frozen.test_columns_table import _link
from tests.integration.labeling.helpers import setup_classifier, start_body
from tests.support.fake_miserver import ORIGIN
from tests.support.frozen import exercises
from tests.support.labeling_fixtures import (  # noqa: F401 - fixture
    Labeling,
    labeling,
    make_version,
    row_key,
)
from tests.support.publish_fixtures import publisher  # noqa: F401 - fixture
from tests.support.send_fixtures import API, sender  # noqa: F401 - fixture
from tests.support.version_fixtures import driver  # noqa: F401 - fixture

CLIENT_CODES = (
    "mistudio_unreachable",
    "mistudio_not_json",
    "mistudio_dataset_exists",
    "mistudio_refused",
)
RUNTIME_KEYS = (
    "millm_base_url",
    "millm_ui_url",
    "millm_lease_ttl_seconds",
    "millm_steering_supported",
    "mistudio_base_url",
    "mistudio_timeout_seconds",
)
LINK_FIELDS = ("mistudio_auroc", "mistudio_ci")
DECLARED = (
    *CLIENT_CODES,
    "mistudio_not_configured",
    "millm_code",
    "millm_status",
    "mistudio_send",
    "millm_model_load",
    *LINK_FIELDS,
    *RUNTIME_KEYS,
    # 2026-11-08 follow-up: the answer fields and protocol names other products read.
    "millm_probe_score",
    "millm_auroc",
    "millm_sae_features",
    "mistudio_render_served",
    "mistudio_roles",
)


def _client_error(literal: str, monkeypatch: pytest.MonkeyPatch) -> str:
    def handler(request: httpx.Request) -> httpx.Response:
        if literal == "mistudio_unreachable":
            raise httpx.ConnectError("refused", request=request)
        if literal == "mistudio_not_json":
            return httpx.Response(200, text="<html>", headers={"content-type": "text/html"})
        if literal == "mistudio_dataset_exists":
            return httpx.Response(409, json={"detail": "exists"})
        return httpx.Response(500, json={"detail": {"code": "boom", "message": "no"}})

    monkeypatch.setattr(mc, "TRANSPORT", httpx.MockTransport(handler))
    client = mc.MiWorkbenchClient("http://bench-frozen.test:8000")
    call: Callable[[], Any] = (
        (lambda: client.download("a/b", None, "train"))
        if literal == "mistudio_dataset_exists"
        else (lambda: client.get_dataset("ds_x"))
    )
    with pytest.raises(mc.MiWorkbenchError) as raised:
        call()
    return raised.value.code


@exercises("DR-18", literals=DECLARED)
async def test_dr18_wire_names_from_the_real_paths(
    client: httpx.AsyncClient,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    literal: str,
) -> None:
    if literal in ("millm_probe_score", "millm_auroc"):
        # A probe-verdict run: the plan's labeler identity names the protocol, and the finished
        # run's reproduction gate records the AUROC miLLM's scores gave.
        probe_millm = request.getfixturevalue("millm")
        send_driver = request.getfixturevalue("sender")
        request.getfixturevalue("operator_name")
        await refreshed(client, send_driver)
        version_id = make_version(data_dir, rows(4))
        planned = await plan(client, probe_body(version_id))
        assert planned.status_code == 200, planned.text
        assert planned.json()["labeler_identity"]["protocol"] == "millm_probe_score"
        started = await client.post(f"{API}/label-runs", json=probe_body(version_id))
        assert started.status_code == 201, started.text
        probe_millm.run_until_done(started.json()["id"])
        run = (await client.get(f"{API}/label-runs/{started.json()['id']}")).json()
        assert run["endpoint_snapshot"]["reproduction"]["millm_auroc"] is not None
        assert literal in (planned.text + str(run))
    elif literal == "millm_sae_features":
        lab = request.getfixturevalue("labeling")
        request.getfixturevalue("operator_name")
        lab.millm.resident = dict(features.LLAMA)
        lab.millm.attachments = [{"sae_id": "sae_l16", "layer": 16}]
        monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
        version_id = make_version(data_dir, features.TEXTS)
        planned = await client.get(
            f"{API}/label-runs/plan", params={"request": json.dumps(features.body(version_id))}
        )
        assert planned.status_code == 200, planned.text
        assert planned.json()["labeler_identity"]["protocol"] == literal
    elif literal == "mistudio_render_served":
        request.getfixturevalue("studio")
        request.getfixturevalue("operator_name")
        made = await client.post(
            f"{API}/reproduction-links", json=link_body(labelled_version(data_dir, gate_texts()))
        )
        assert made.status_code == 201, made.text
        assert literal in made.json()["scoring_form"]["mistudio"]
    elif literal == "mistudio_roles":
        send_driver = request.getfixturevalue("sender")
        request.getfixturevalue("operator_name")
        _, send = await sent_set(client, send_driver)
        assert send[literal], "the send answer maps each role to miStudio's role"
    elif literal in CLIENT_CODES:
        assert _client_error(literal, monkeypatch) == literal
    elif literal == "mistudio_not_configured":
        monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
        response = await client.post(
            f"{API}/reward-marks", json={"mistudio_probe_id": "pm_x", "reason": "r"}
        )
        assert response.status_code == 409 and response.json()["error"]["code"] == literal
    elif literal in ("millm_code", "millm_status"):
        refusal = pp.refusal_for(
            RowError("no such probe", 404, server_code="probe_not_found", details={})
        )
        assert refusal is not None
        assert refusal.details["millm_code"] == "PROBE_NOT_FOUND"
        assert refusal.details["millm_status"] == 404
        assert literal in refusal.details
    elif literal == "mistudio_send":
        assert get_job_kind(literal).name == literal
    elif literal == "millm_model_load":
        actions = (await client.get("/api/v1/approvals/actions")).json()["actions"]
        assert literal in actions
    elif literal in LINK_FIELDS:
        link_id = _link(labelled_version(data_dir, [("a", "a"), ("b", "b")]))
        answer = (await client.get(f"{API}/reproduction-links/{link_id}")).json()
        assert answer["mistudio_auroc"] == 0.9 and answer["mistudio_ci"] == [0.89, 0.91]
        assert literal in answer
    else:
        value = {
            "millm_base_url": "http://serve.test:8000",
            "millm_ui_url": "https://serve.test",
            "millm_lease_ttl_seconds": "900",
            "millm_steering_supported": "false",
            "mistudio_base_url": "http://bench.test:8000",
            "mistudio_timeout_seconds": "12",
        }[literal]
        response = await client.put(f"/api/v1/settings/{literal}", json={"value": value})
        assert response.status_code == 200, response.text
        assert getattr(get_settings(), literal) not in (None, "")


DR34_STEERING = "profile:dr34-frozen"  # not the fallback, so only the block can have supplied it


@exercises("DR-34", literals=("millm",))
async def test_dr34_millm_response_block_is_read(
    client: httpx.AsyncClient,
    data_dir: Path,
    labeling: Labeling,  # noqa: F811 - fixture
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
    literal: str,
) -> None:
    """DR-34: miServer's extension block keeps the key ``"millm"`` in batch objects, batch output
    lines and completion responses, and miDataworks reads it at three sites. Each is driven through
    the real label-run path on the fake miServer, whose bodies carry the block under ``literal``:

    1. ``label_run_engine._poll_batch``: ``batch["millm"]["waiting_reason"] == "lease_unavailable"``
       while our lease is still alive makes the run re-attach the batch to it (nothing else would:
       the lease check passes);
    2. ``label_run_engine._batch_record``: ``line["response"]["millm"]["headers"]`` gives each
       label its ``X-miLLM-Steering`` value, not the fallback;
    3. ``SaeActivationsClient.read``: ``response["millm"]["sae_activations"]`` becomes the tag's
       positions and features.
    """
    fake = labeling.millm
    assert literal == "millm"

    # --- 1 and 2: one batch run, detached from its live lease on the first poll -------------
    fake.batch_polls_to_complete = 2
    fake.steering_header = DR34_STEERING
    detached: list[int] = []

    def detach(batch: dict[str, Any]) -> None:
        # The batch loses its lease while ours stays alive: only the block's waiting_reason says
        # so. After a few unanswered polls, re-attach it here so a broken reader ends (red), not
        # hangs.
        if not detached:
            detached.append(1)
            batch["lease_id"] = "lease-gone"
        elif batch["lease_id"] == "lease-gone":
            detached.append(1)
            if len(detached) > 3:
                batch["lease_id"] = next(iter(fake.leases))

    fake.on_batch_poll = detach
    version_id, template_id = await setup_classifier(
        client, labeling, rows=[f"dr34 row {i}" for i in range(3)]
    )
    started = await client.post(
        "/api/v1/label-runs", json=start_body(version_id, template_id, transport="batch")
    )
    assert started.status_code == 201, started.text
    final = labeling.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    (created,) = fake.calls("/v1/batches", "POST")
    batch_id = final.endpoint_snapshot["batches"][0]["id"]
    reattached = fake.calls(f"/v1/batches/{batch_id}/lease", "POST")
    assert [r.headers["x-millm-lease"] for r in reattached] == [created.headers["x-millm-lease"]]
    assert len(fake.calls("/api/models/7/lease", "POST")) == 1  # the lease was never re-taken
    assert len(detached) == 1, "the run polled on without reading waiting_reason"
    items = (await client.get(f"/api/v1/label-runs/{final.id}/labels")).json()["items"]
    assert len(items) == 3
    assert {i["steering_state"] for i in items} == {DR34_STEERING}
    fake.on_batch_poll = None

    # --- 3: a feature-tag run reads the activations block -----------------------------------
    fake.resident = dict(features.LLAMA)
    fake.attachments = [{"sae_id": "sae_l16", "layer": 16}]
    monkeypatch.setattr(get_settings(), "millm_base_url", ORIGIN)
    texts = [f"dr34 headline {i}" for i in range(2)]
    tag_version = make_version(data_dir, texts)
    tagged = await client.post("/api/v1/label-runs", json=features.body(tag_version))
    assert tagged.status_code == 201, tagged.text
    done = labeling.run_until_done(tagged.json()["id"])
    assert done.state == "completed", done.error
    assert done.counts == {"tagged": 2}
    spec = {"sae_id": "sae_l16", "top_k": 4, "positions": "last"}
    stored = features.labels_of(done.id)
    for text in texts:
        served = fake._activations(spec, text)
        assert stored[row_key(text)].parsed_value["positions"] == served["positions"]


@exercises("DR-49", literals=("millm_model", "millm_steering"))
async def test_dr49_millm_model_and_millm_steering_are_read(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    millm: Labeling,  # noqa: F811 - fixture
    sender: Any,  # noqa: F811 - fixture; after millm, as the probe-verdict tests take it
    literal: str,
) -> None:
    """DR-49: two names of miServer's public surface this repository keeps as they are.

    ``millm_model``: a probe-verdict run records the model miServer reported while scoring under
    this key of the run's stored ``endpoint_snapshot`` (``label_run_engine``), and the API serves the
    snapshot with it (the Labeling screen reads ``endpoint_snapshot.millm_model``). Driven through
    the real run on the fake miServer.

    ``millm_steering``: the field of a stream's terminal chunk that carries the steering report
    (miServer FR-28.3.9). ``generation.steering.STREAM_FIELD`` is the name this repository reads it
    under; a chunk as miServer sends it is read through that constant and judged by the real check.
    """
    if literal == "millm_steering":
        from src.services.generation import steering

        assert steering.STREAM_FIELD == literal
        expected = steering.Expected("none")
        chunk: dict[str, Any] = {"object": "chat.completion.chunk", "choices": [], literal: "none"}
        reported = steering.parse_steering_header(chunk.get(steering.STREAM_FIELD))
        assert steering.check_reported_state(expected, reported).result == "match"
        # Under any other field name the report is not found: unreported, never "unsteered".
        moved: dict[str, Any] = {**chunk, "miserver_steering": chunk[literal]}
        del moved[literal]
        absent = steering.parse_steering_header(moved.get(steering.STREAM_FIELD))
        assert steering.check_reported_state(expected, absent).result == "unreported"
        return

    assert literal == "millm_model"
    await refreshed(client, sender)
    version_id = make_version(data_dir, rows(4))
    started = await client.post(f"{API}/label-runs", json=probe_body(version_id))
    assert started.status_code == 201, started.text
    final = millm.run_until_done(started.json()["id"])
    assert final.state == "completed", final.error
    reported_model = {
        "hf_id": PROBE_MODEL["repo_id"],
        "revision": PROBE_MODEL["revision"],
        "dtype": "bfloat16",
        "quantization": PROBE_MODEL["quantization"],
    }
    assert final.endpoint_snapshot[literal] == reported_model
    served = await client.get(f"{API}/label-runs/{final.id}")
    assert served.status_code == 200, served.text
    assert served.json()["endpoint_snapshot"][literal] == reported_model
    assert [k for k in final.endpoint_snapshot if k.endswith("_model")] == [literal]
