"""The miWorkbench client and the capability probe (FTASKS 5.1 - 5.5)."""

from __future__ import annotations

import inspect
import json

import httpx
import pytest

from src.clients import miworkbench_client as mc
from src.services.detector_sets import capabilities
from tests.support.fake_miworkbench import BASE, FakeMiWorkbench


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMiWorkbench:
    f = FakeMiWorkbench()
    monkeypatch.setattr(mc, "TRANSPORT", f.transport())
    capabilities.clear_cache()
    return f


def test_download_has_no_token_parameter_and_sends_exactly_three_keys(
    fake: FakeMiWorkbench,
) -> None:
    params = set(inspect.signature(mc.MiWorkbenchClient.download).parameters)
    assert not any("token" in p for p in params), params
    with mc.MiWorkbenchClient(BASE) as client:
        client.download("mistudio/humor-v1", None, "train")
    [call] = fake.calls("POST", "/api/v1/datasets/download")
    assert set(call.body) == {"repo_id", "config", "split"}
    assert "authorization" not in call.headers


def test_revision_is_sent_only_when_asked(
    fake: FakeMiWorkbench, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support import fake_miworkbench

    # a miWorkbench that serves XR-3 accepts the key; today's refuses it (extra="forbid")
    monkeypatch.setattr(
        fake_miworkbench, "DOWNLOAD_FIELDS", fake_miworkbench.DOWNLOAD_FIELDS | {"revision"}
    )
    with mc.MiWorkbenchClient(BASE) as client:
        client.download("mistudio/humor-v1", None, "train", revision="a" * 40)
    assert fake.calls("POST", "/api/v1/datasets/download")[0].body["revision"] == "a" * 40


def test_errors_are_distinct(fake: FakeMiWorkbench) -> None:
    with mc.MiWorkbenchClient(BASE) as client:
        fake.fail(
            "POST /datasets/download", status=409, body={"detail": "Dataset x already exists"}
        )
        with pytest.raises(mc.MiWorkbenchDatasetExists) as exists:
            client.download("x/y", None, "train")
        assert exists.value.detail == "Dataset x already exists"
        fake.fail("GET /datasets/{id}", non_json=True)
        with pytest.raises(mc.MiWorkbenchNotJson):
            client.get_dataset("d1")
        fake.fail("GET /datasets/{id}", drop=True)
        with pytest.raises(mc.MiWorkbenchUnreachable):
            client.get_dataset("d1")
        fake.fail(
            "POST /probe-monitors/datasets", status=422, body={"detail": "mapping covers nothing"}
        )
        with pytest.raises(mc.MiWorkbenchRefused) as refused:
            client.register_view({"name": "x"})
        assert refused.value.status == 422 and refused.value.detail == "mapping covers nothing"


def test_a_timeout_reads_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(mc, "TRANSPORT", httpx.MockTransport(slow))
    with mc.MiWorkbenchClient(BASE) as client, pytest.raises(mc.MiWorkbenchTimeout) as err:
        client.list_runs()
    assert err.value.code == "mistudio_unreachable"


def test_a_missing_report_is_none(fake: FakeMiWorkbench) -> None:
    with mc.MiWorkbenchClient(BASE) as client:
        assert client.get_report("pm_gone") is None


def test_capabilities_read_the_api_openapi_path_and_report_absent_fields(
    fake: FakeMiWorkbench,
) -> None:
    with mc.MiWorkbenchClient(BASE) as client:
        caps = capabilities.read(client)
    [call] = fake.requests
    assert call.path == "/api/openapi.json"
    # the recorded miWorkbench schema (2026-10-07) serves none of XR-1, XR-2, XR-3
    assert (caps.dataset_version_manifest, caps.download_revision, caps.per_row_scores) == (
        False,
        False,
        False,
    )
    assert "not served by miWorkbench" in caps.as_dict()["reasons"]["dataset_version_manifest"]


def test_capabilities_flip_when_the_schema_serves_them(fake: FakeMiWorkbench) -> None:
    doc = json.loads(json.dumps(fake.openapi_doc))
    doc["components"]["schemas"]["ProbeDatasetCreate"]["properties"][
        "dataset_version_manifest"
    ] = {}
    doc["components"]["schemas"]["DatasetDownloadRequest"]["properties"]["revision"] = {}
    doc["paths"]["/api/v1/probe-monitors/probes/{probe_id}/evaluations/{dataset_id}/scores"] = {
        "get": {}
    }
    caps = capabilities.from_openapi(BASE, doc)
    assert (caps.dataset_version_manifest, caps.download_revision, caps.per_row_scores) == (
        True,
        True,
        True,
    )


def test_the_offline_scorer_is_not_a_per_row_score_route(fake: FakeMiWorkbench) -> None:
    assert "/api/v1/probe-monitors/probes/{probe_id}/score" in fake.openapi_doc["paths"]
    assert capabilities.from_openapi(BASE, fake.openapi_doc).per_row_scores is False


def test_a_non_json_schema_is_not_json_never_not_served(fake: FakeMiWorkbench) -> None:
    fake.fail("GET /api/openapi.json", non_json=True)
    with mc.MiWorkbenchClient(BASE) as client, pytest.raises(mc.MiWorkbenchNotJson):
        capabilities.read(client, fresh=True)


def test_capabilities_are_cached(fake: FakeMiWorkbench) -> None:
    with mc.MiWorkbenchClient(BASE) as client:
        capabilities.read(client)
        capabilities.read(client)
        capabilities.read(client, fresh=True)
    assert fake.count("GET", "/api/openapi.json") == 2


def test_the_fake_refuses_unknown_registration_keys(fake: FakeMiWorkbench) -> None:
    with mc.MiWorkbenchClient(BASE) as client, pytest.raises(mc.MiWorkbenchRefused) as err:
        client.register_view({"name": "x", "dataset_version_manifest": {}})
    assert err.value.status == 422
