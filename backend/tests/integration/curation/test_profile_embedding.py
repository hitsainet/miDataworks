"""The profile's embedding near-duplicate figure (004 FTASKS 15.5, 2026-10-17), CPU only.

A fake OpenAI-compatible embeddings server behind 005's real ``EndpointCaller``; the role is set in
the real ``dw_endpoint_roles`` table and resolved by 005's resolver, as production does. The fake's
vectors depend only on a text's first word, while the lexical MinHash figure sees disjoint words: a
figure computed on the lexical basis cannot match here."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from src.clients import endpoint_caller
from src.core.database import sync_session_factory
from src.operators import endpoint_port
from src.services.curation import report_service
from src.services.curation.codes import ReportInput
from src.services.curation.kinds import compute_for
from src.services.curation.profile_service import embedding_identity
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version
from tests.support.generation_fixtures import set_role
from tests.unit.curation.test_dedup_embedding import KEY, FakeEmbeddings, Leases, Resolver

BASE = "http://embed.test/v1"
ROLES = {"text": "content", "note": "metadata"}
# three first words -> three near-identical groups by embedding; the rest of each text is unique
TEXTS = [f"{('alpha', 'bravo', 'charlie')[i % 3]} w{i}a w{i}b w{i}c w{i}d" for i in range(12)]


@pytest.fixture
def server(clean_db: None, data_dir: Path) -> Iterator[FakeEmbeddings]:
    fake = FakeEmbeddings()
    previous = endpoint_caller.install_transport(lambda origin: httpx.MockTransport(fake.handle))
    # 005's ports, installed explicitly: a fake miLLM endpoint and NO lease (the ``leases``
    # fixture installs one where a test asks). Both slots are process-global and a label-run
    # worker installs the real ones and never undoes it, so a fixture that set only the resolver
    # passed alone and failed in CI after such a test (2026-10-18; ``real_ports`` below).
    old_resolver = endpoint_port.install_resolver(Resolver())
    old_leases = endpoint_port.install_lease_manager(endpoint_port.NoLeaseManager())
    yield fake
    endpoint_caller.install_transport(previous)
    endpoint_port.install_resolver(old_resolver)
    endpoint_port.install_lease_manager(old_leases)


@pytest.fixture
def real_ports() -> Iterator[None]:
    """What a label-run worker leaves behind: 005's real resolver and lease manager installed."""
    from src.services import labeling_ports

    old_resolver = endpoint_port.resolver()
    old_leases = endpoint_port.lease_manager()
    labeling_ports.install()
    yield
    endpoint_port.install_resolver(old_resolver)
    endpoint_port.install_lease_manager(old_leases)


@pytest.fixture
def leases() -> Iterator[Leases]:
    manager = Leases()
    old = endpoint_port.install_lease_manager(manager)
    yield manager
    endpoint_port.install_lease_manager(old)


def _version() -> str:
    with sync_session_factory()() as db:
        rows = [{"text": t, "note": None} for t in TEXTS]
        return str(make_version(db, with_system_columns(rows), ROLES).id)


def _profile(version_id: str) -> dict:
    with sync_session_factory()() as db:
        identity = embedding_identity(db)
    _, report = report_service.find_or_run_inline(
        "profile",
        [ReportInput(version_id)],
        {"sample_size": None, "embeddings": identity},
        7,
        compute_for("profile"),
        started_by="t",
        origin="operator",
    )
    return dict(report.result["figures"])


def test_unconfigured_is_not_computed_and_never_zero(server: FakeEmbeddings) -> None:
    with sync_session_factory()() as db:
        db.execute(text("DELETE FROM dw_endpoint_roles WHERE role = 'embeddings'"))
        db.commit()
    figure = _profile(_version())["near_duplicates_embedding"]
    assert figure["status"] == "not_computed" and "No embeddings endpoint" in figure["reason"]
    assert "rows" not in figure and server.calls == []


def test_configured_computes_the_figure_on_the_embedding_basis(server: FakeEmbeddings) -> None:
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    figures = _profile(_version())
    figure = figures["near_duplicates_embedding"]
    assert figure["status"] == "computed", figure
    assert figure["basis"] == "embedding" and figure["threshold"] == 0.95
    assert figure["groups"] == 3 and figure["rows"] == 12 - 3
    assert figure["embedding"]["model"] == "embed-1" and KEY not in str(figure)
    assert figures["near_duplicates"]["rows"] == 0  # the lexical figure sees no near duplicate
    sent = sorted(t for c in server.calls for t in c["body"]["input"])
    assert sent == sorted(TEXTS)


def test_the_role_is_part_of_the_profile_identity(server: FakeEmbeddings) -> None:
    with sync_session_factory()() as db:
        db.execute(text("DELETE FROM dw_endpoint_roles WHERE role = 'embeddings'"))
        db.commit()
    version = _version()
    assert _profile(version)["near_duplicates_embedding"]["status"] == "not_computed"
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    assert _profile(version)["near_duplicates_embedding"]["status"] == "computed"


def test_a_failing_endpoint_is_reported_not_zero(server: FakeEmbeddings) -> None:
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    server.script = [httpx.Response(500, json={"error": {"code": "boom"}})] * 10
    figure = _profile(_version())["near_duplicates_embedding"]
    assert figure["status"] == "not_computed" and "did not answer" in figure["reason"]


async def test_the_route_recomputes_when_the_role_is_configured(
    client: httpx.AsyncClient, server: FakeEmbeddings, operator_name: str
) -> None:
    """The REST route puts the role's identity into the profile's parameters (the wiring)."""
    with sync_session_factory()() as db:
        db.execute(text("DELETE FROM dw_endpoint_roles WHERE role = 'embeddings'"))
        db.commit()
    base = f"/api/v1/versions/{_version()}/profile"
    first = await client.post(base, json={})
    assert first.status_code == 200 and first.json()["outcome"] == "inline", first.text
    assert (await client.post(base, json={})).json()["outcome"] == "existing"
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    again = await client.post(base, json={})
    assert again.json()["outcome"] == "inline"
    figure = (await client.get(base)).json()["result"]["figures"]["near_duplicates_embedding"]
    assert figure["status"] == "computed" and figure["groups"] == 3


def test_the_profile_takes_the_shared_lease_and_sends_it_on_every_request(
    server: FakeEmbeddings, leases: Leases
) -> None:
    """live2 finding (2026-10-18): the profile called miLLM with no lease (39 requests with none
    held), so another holder could swap the model mid-profile (ADR-011/012, P-05)."""
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    figure = _profile(_version())["near_duplicates_embedding"]
    assert figure["status"] == "computed" and figure["pinned"] is True
    assert (leases.acquired, leases.released) == (1, 1)
    assert server.calls and all(
        c["headers"].get("x-millm-lease") == "lease-xyz" for c in server.calls
    )


def test_a_server_without_leases_is_recorded_unpinned(server: FakeEmbeddings) -> None:
    """A generic OpenAI-compatible server offers no lease (standalone principle): the figure is
    still computed and says it ran unpinned."""
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    figure = _profile(_version())["near_duplicates_embedding"]
    assert figure["status"] == "computed" and figure["pinned"] is False
    assert all("x-millm-lease" not in c["headers"] for c in server.calls)


def test_the_figure_does_not_depend_on_ports_another_test_left_installed(
    real_ports: None, server: FakeEmbeddings
) -> None:
    """Regression (CI, 2026-10-18): run after a label-run worker had installed 005's real ports,
    the profile tried the real lease client against the fake server and read not_computed."""
    set_role("embeddings", BASE, "embed-1", protocol="openai_embeddings", api_key=KEY)
    figure = _profile(_version())["near_duplicates_embedding"]
    assert figure["status"] == "computed", figure
