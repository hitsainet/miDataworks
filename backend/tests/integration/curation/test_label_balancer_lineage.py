"""The label balancer's warning travels with the version (004 FR-004.56; FTASKS 18.3).

A real build through 003's executor and 002's finalize, then a child version built FROM it: both
list the step in ``GET /versions/{id}/lineage``; the child's entry is marked inherited; 008's
assembled publish inputs carry the note ``N-label_balanced`` and the manifest lineage record."""

from __future__ import annotations

from pathlib import Path

import httpx

from src.core.database import sync_session_factory
from src.models.version import Version
from src.services.curation.label_balance import LEAKAGE_WARNING
from tests.integration.curation import test_split_build as split_build
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

V = "/api/v1/versions"
_body, _rows = split_build._body, split_build._rows
driver = split_build.driver  # the real-executor build driver fixture


async def _run(
    client: httpx.AsyncClient, driver: RealDriver, ds: str, rev: str, inputs: list
) -> str:
    response = await request(client, ds, rev, inputs, seed=1)
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed", driver.step_results
    with sync_session_factory()() as db:
        return str(
            db.query(Version)
            .filter(Version.dataset_id == ds)
            .order_by(Version.number.desc())
            .first()
            .id
        )


async def test_the_warning_is_on_the_version_its_child_and_the_publish_inputs(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": _rows(90)})  # labels a:b = 60:30
    ds, rev = await setup(client, _body(("label_balancer", {})))
    parent = await _run(client, driver, ds, rev, [src(source)])
    other = await client.post(
        "/api/v1/recipes",
        json={
            "name": "child-recipe",
            "body": _body(("metadata_value_filter", {"column": "label", "values": ["zzz"]})),
        },
    )
    assert other.status_code == 201, other.text
    child = await _run(
        client,
        driver,
        ds,
        other.json()["head_revision_id"],
        [{"kind": "version", "version_id": parent}],
    )

    own = (await client.get(f"{V}/{parent}/lineage")).json()["label_balancing"]
    assert len(own) == 1 and own[0]["version_id"] == parent and own[0]["inherited"] is False
    assert own[0]["label_column"] == "label" and own[0]["rows_dropped"] == 30
    assert own[0]["warning"] == LEAKAGE_WARNING
    inherited = (await client.get(f"{V}/{child}/lineage")).json()["label_balancing"]
    assert [(s["version_id"], s["inherited"]) for s in inherited] == [(parent, True)]

    from src.services.publishing import check_inputs
    from src.services.publishing.checks import Outcome, evaluate_checks

    with sync_session_factory()() as db:
        version = db.get(Version, child)
        assembled = check_inputs.assemble(
            db, version, label_column="label", visibility="private", token_scope="write"
        )
    assert [s["version_id"] for s in assembled.lineage["label_balancing"]] == [parent]
    notes = [o for o in evaluate_checks(assembled.inputs) if o.check == "N-label_balanced"]
    assert len(notes) == 1 and notes[0].outcome is Outcome.NOTE


async def test_a_version_without_the_step_lists_nothing(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": _rows(30)})
    ds, rev = await setup(
        client, _body(("metadata_value_filter", {"column": "label", "values": ["zzz"]}))
    )
    version = await _run(client, driver, ds, rev, [src(source)])
    assert (await client.get(f"{V}/{version}/lineage")).json()["label_balancing"] == []
