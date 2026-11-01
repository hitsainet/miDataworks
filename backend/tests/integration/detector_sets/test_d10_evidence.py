"""D-10: gate results and evidence notes before a send, and FR-017.11's definition notes (017
FTASKS 10.8; FR-017.49, FR-017.11, FR-017.7; TD-017.27).

Real versions, the real check route. D-10 NOTES and never refuses: with nothing measured it reads
"not measured" and the set stays sendable; a report on a parent version is named as measured
elsewhere, never counted; a diversity report that falls notes and the send stays allowed; an
evaluation role mixing generated and source rows is noted; a behaviour set with no second
opinion says so, with the rule-less words when its training build ran no rule set; and the
definition notes read "built against v1, now v2", the archive and a meaning mismatch from the fake
miStudio catalogue (or "not verified" without one). Controls M27 x2, M28b.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.clients import mistudio_client as mc
from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.models.generation import DiversityReport
from src.models.version import Version
from tests.support.detector_fixtures import humor_versions, labelled_rows, set_body, version
from tests.support.fake_mistudio import BASE, FakeMiStudio, catalogue_head

API = "/api/v1"
KEY = "fixture-alpha"
MEANING = "Most readers would find the text funny"


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMiStudio]:
    fake = FakeMiStudio()
    monkeypatch.setattr(mc, "TRANSPORT", fake.transport())
    monkeypatch.setattr(get_settings(), "mistudio_base_url", BASE)
    yield fake


def diversity(version_id: str, verdict: str) -> str:
    report_id = f"dvr_{uuid.uuid4().hex[:20]}"
    with sync_session_factory()() as db:
        db.add(
            DiversityReport(
                id=report_id,
                version_id=version_id,
                column="text",
                method_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                method={},
                splits=["train"],
                sample_size=10,
                seed=0,
                clustering={},
                figures={},
                checks=[],
                verdict=verdict,
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.commit()
    return report_id


def child_of(parent_id: str, child_id: str) -> None:
    with sync_session_factory()() as db:
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        row = db.get(Version, child_id)
        assert row is not None
        row.parent_version_id = parent_id
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.commit()


async def checked(client: httpx.AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    response = await client.post(f"{API}/detector-sets/{created.json()['id']}/checks")
    assert response.status_code == 200, response.text
    out: dict[str, Any] = response.json()
    return out


def d10(result: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = next(o for o in result["outcomes"] if o["code"] == "D-10")
    return found


async def test_nothing_measured_reads_not_measured_and_the_set_stays_sendable(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    v = humor_versions()
    result = await checked(client, set_body(v, name="d10-plain"))
    outcome = d10(result)
    assert outcome["outcome"] == "note"
    assert result["send_allowed"] is True and result["first_refusal"] is None
    notes = outcome["details"]["notes"]
    assert any("diversity not measured" in n for n in notes)
    assert any("contamination not measured" in n for n in notes)
    assert not any("Second opinion" in n for n in notes), "a set with no behaviour is not asked"
    assert [o["code"] for o in result["outcomes"]] == [f"D-{i}" for i in range(1, 12)]


async def test_a_report_counts_only_on_the_roles_exact_version(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    v = humor_versions()
    parent = version({"train": labelled_rows(40, seed=9, tag="pa")}, dataset_name="d10-parent")
    child_of(parent, v["train"])
    diversity(parent, "holds")
    diversity(v["ood"], "falls")  # a falling verdict notes; the send stays allowed
    result = await checked(client, set_body(v, name="d10-elsewhere"))
    outcome = d10(result)
    by_role = {r["role"]: r for r in outcome["details"]["roles"]}
    train = next(r for r in by_role.values() if r["kind"] == "train")
    assert train["diversity"] is None
    assert train["diversity_elsewhere"] == "d10-parent v1"
    assert any("not measured (measured on" in f for f in train["figures"])
    ood = next(r for r in by_role.values() if r["kind"] == "ood_eval")
    assert ood["diversity"]["verdict"] == "falls"
    assert any("diversity falls" in n for n in outcome["details"]["notes"])
    assert outcome["outcome"] == "note" and result["send_allowed"] is True


async def test_a_behaviour_set_without_a_second_opinion_says_its_labels_rest_on_the_plan(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    v = humor_versions()
    body = set_body(v, name="d10-second", behaviour_key=KEY)
    outcome = d10(await checked(client, body))
    notes = outcome["details"]["notes"]
    assert any(
        n.startswith("Second opinion not measured: the behaviour has no rule signal") for n in notes
    ), notes
    assert outcome["details"]["second_opinion"] == {"runs": [], "rule_signal": False}


async def test_the_definition_moved_archived_and_a_meaning_mismatch_are_named(
    client: httpx.AsyncClient, operator_name: str, catalogue: FakeMiStudio
) -> None:
    v = humor_versions()
    catalogue.behaviours[KEY] = catalogue_head(KEY, version=1, positive_meaning=MEANING)
    body = set_body(
        v,
        name="d10-drift",
        behaviour_key=KEY,
        behaviour_definition={
            "key": KEY,
            "definition_version": 1,
            "positive_meaning": MEANING,
            "verified": True,
            "source": "catalogue",
        },
    )
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    set_id = created.json()["id"]

    async def notes() -> tuple[dict[str, Any], list[str]]:
        response = await client.post(f"{API}/detector-sets/{set_id}/checks")
        result = response.json()
        return result, list(d10(result)["details"]["notes"])

    result, found = await notes()
    assert not any("now v" in n or "mismatch" in n for n in found), found
    catalogue.behaviours[KEY] = catalogue_head(
        KEY, version=2, positive_meaning="the text names a number", archived=True
    )
    result, found = await notes()
    assert "The set was made against v1, now v2." in found
    assert f"Behaviour {KEY!r} is archived in the catalogue." in found
    assert any(
        n.startswith("Meaning mismatch: the catalogue's positive meaning is 'the text names a "
                     "number'") and MEANING in n
        for n in found
    ), found  # fmt: skip
    assert result["send_allowed"] is True and d10(result)["outcome"] == "note"


async def test_without_a_catalogue_the_definition_reads_not_verified(
    client: httpx.AsyncClient, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    v = humor_versions()
    outcome = d10(await checked(client, set_body(v, name="d10-unverified", behaviour_key=KEY)))
    assert any(
        n.startswith(f"Definition of {KEY!r}: not verified") for n in outcome["details"]["notes"]
    )


async def test_an_evaluation_role_mixing_generated_and_source_rows_is_noted(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """FR-017.7: real-text tests stay their own role; a role holding both origins is noted."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.core.storage import resolve_under_data_dir

    v = humor_versions()
    with sync_session_factory()() as db:
        ood = db.get(Version, v["ood"])
        assert ood is not None
        (split,) = [s for s in ood.splits if s["name"] == "test"]
    path = resolve_under_data_dir(split["path"])
    table = pq.read_table(path)
    origin = ["generated" if i % 3 == 0 else "source" for i in range(table.num_rows)]
    pq.write_table(
        table.set_column(
            table.schema.get_field_index("_dw_origin"), "_dw_origin", pa.array(origin)
        ),
        path,
    )
    outcome = d10(await checked(client, set_body(v, name="d10-mixed")))
    generated = origin.count("generated")
    assert any(
        f"mixes {generated:,} generated and {table.num_rows - generated:,} source" in n
        for n in outcome["details"]["notes"]
    ), outcome["details"]["notes"]


async def test_d9_reports_each_genre_held_out_beside_its_own_figure(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """OQ-017-3 (a): a role carrying ``genre`` gets the leave-one-genre-out figure from the one
    fitter, reported beside its own; D55's rule still reads its own figure."""
    from tests.integration.curation.test_text_baseline_grouped import genre_rows

    rows = genre_rows(("email", "chat", "report"), n=300)
    for r in rows:
        r["label"] = "humorous" if r["label"] == "yes" else "not_humorous"
        r["pair_id"] = r["text"]
        r["format"] = "x"
    v = humor_versions()
    v["train"] = version(
        {"train": rows[:240], "test": rows[240:]},
        dataset_name="d9-genres",
        held_out={"test": True},
        roles={"text": "content", "label": "metadata", "format": "metadata",
               "pair_id": "metadata", "genre": "metadata"},
    )  # fmt: skip
    result = await checked(client, set_body(v, name="d9-genre"))
    d9 = next(o for o in result["outcomes"] if o["code"] == "D-9")
    train = next(r for r in d9["details"]["roles"] if r["role"] == "train")
    held = train["held_out_by_value"]
    assert held["by"] == "genre" and held["values"] == 3 and held["auroc"] is not None
    assert train["auroc"] is not None and train["auroc"] > held["auroc"] + 0.2
    assert ", each genre held out: " in d9["figure"]


async def test_a_set_names_its_benchmarks_and_the_catalogue_follows(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """TD-017.30: benchmarks are data on the set (create, update, clear); an unknown source is
    refused; the contamination catalogue is what live sets name."""
    from tests.support import db_factories

    with sync_session_factory()() as db:
        bench = db_factories.source(db, repo_id="org/benchmark")
        db.commit()
        bench_id = str(bench.id)
    v = humor_versions()
    created = await client.post(
        f"{API}/detector-sets",
        json={**set_body(v, name="d10-bench"), "benchmark_source_ids": [bench_id]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["benchmark_source_ids"] == [bench_id]
    catalogue = (await client.get(f"{API}/curation/benchmarks")).json()["items"]
    assert [(c["source_id"], c["named_by_sets"]) for c in catalogue] == [(bench_id, ["d10-bench"])]
    unknown = await client.patch(
        f"{API}/detector-sets/{created.json()['id']}", json={"benchmark_source_ids": ["src_nope"]}
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "benchmark_not_found"
    cleared = await client.patch(
        f"{API}/detector-sets/{created.json()['id']}", json={"benchmark_source_ids": []}
    )
    assert cleared.status_code == 200 and cleared.json()["benchmark_source_ids"] == []
    assert (await client.get(f"{API}/curation/benchmarks")).json()["items"] == []
