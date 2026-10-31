"""A generation run says what it is for, when it ran and what it produced (operator request
2026-10-21: "when did it start? when did it end. total duration, what was the behaviour").

Every field is read through the real routes (get AND list) from rows the product writes: the run's
jobs, a candidate version built through 002's real orchestrator, detector sets and a rubric made
through their own routes, and a minimal-pair chain row as 009's ``start`` records it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models.dataset import Dataset
from src.models.detector_set import DetectorSet, DetectorSetRole
from src.models.minimal_pair_chain import MinimalPairChain
from src.models.version import Version
from tests.integration.generation.test_candidate_and_filter_builds import (  # noqa: F401
    build_candidate,
    driver,
)
from tests.support.generation_fixtures import (
    JUDGE_MODEL,
    Gen,
    make_version,
    respond_template,
    run_body,
    texts,
    version_table,
)
from tests.support.operator_build_driver import RealDriver

RUNS = "/api/v1/generation-runs"
RUBRIC = {
    "style": "binary",
    "messages": [
        {"role": "user", "content": "Is ROW high stakes?\nROW: {text}\nVERDICT: yes or no."}
    ],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


async def started(client: httpx.AsyncClient, version: str, **kw: Any) -> str:
    template = await respond_template(client)
    response = await client.post(RUNS, json=run_body(version, template, **kw))
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def get(client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    response = await client.get(f"{RUNS}/{run_id}")
    assert response.status_code == 200, response.text
    listed = await client.get(RUNS)
    assert listed.status_code == 200, listed.text
    (item,) = [r for r in listed.json()["items"] if r["id"] == run_id]
    out = dict(response.json())
    # The list carries the same context as the single read (the tiles read the list).
    for key in ("timing", "input_version", "behaviour", "behaviours", "candidate_versions"):
        assert item[key] == out[key], key
    return out


def dataset_of(version_id: str) -> tuple[str, str, int]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        ds = db.get(Dataset, version.dataset_id)
        assert ds is not None
        return str(ds.id), ds.name, int(version.number)


def detector_set(
    version_id: str, *, key: str | None, meaning: str, name: str, archived: bool = False
) -> str:
    with sync_session_factory()() as db:
        row = DetectorSet(
            id=new_id("dts"),
            name=name,
            positive_meaning=meaning,
            behaviour_key=key,
            archived=archived,
            created_by="T",
            created_by_origin="operator",
        )
        db.add(row)
        db.flush()
        db.add(
            DetectorSetRole(
                id=new_id("dsr"),
                set_id=row.id,
                role="train",
                position=0,
                version_id=version_id,
                split="train",
                input_column="prompt",
                label_column="label",
                label_mapping={"yes": "positive"},
            )
        )
        db.commit()
        return row.id


async def test_a_queued_then_finished_run_reports_when_it_ran(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(4)))
    run_id = await started(client, version)
    queued = await get(client, run_id)
    timing = queued["timing"]
    assert queued["state"] == "queued"
    assert timing["queued_at"] == queued["created_at"]
    assert timing["started_at"] is None and timing["finished_at"] is None
    assert timing["total_seconds"] is None and timing["queued_seconds"] is None
    assert timing["attempts"] == 1

    gen.run_until_done(run_id)
    done = await get(client, run_id)
    timing = done["timing"]
    assert done["state"] == "completed"
    started_at = datetime.fromisoformat(timing["started_at"])
    finished_at = datetime.fromisoformat(timing["finished_at"])
    assert timing["finished_at"] == done["completed_at"]
    assert started_at >= datetime.fromisoformat(timing["queued_at"])
    assert timing["total_seconds"] == round((finished_at - started_at).total_seconds(), 3)
    assert timing["active_seconds"] is not None and timing["active_seconds"] >= 0
    assert timing["queued_seconds"] is not None and timing["queued_seconds"] >= 0


async def test_names_replace_ids_and_no_behaviour_reads_none(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(4)))
    run_id = await started(client, version)
    out = await get(client, run_id)
    dataset_id, name, number = dataset_of(version)
    assert out["input_version"] == {
        "id": version,
        "dataset_id": dataset_id,
        "dataset_name": name,
        "number": number,
        "created_at": out["input_version"]["created_at"],
    }
    assert out["respond_template"]["name"] == "respond-v1"
    assert out["respond_template"]["kind"] == "respond"
    assert set(out["respond_template"]["sampling"]) >= {"temperature", "top_p", "max_tokens"}
    assert out["expand_template"] is None
    assert out["behaviour"] is None and out["behaviours"] == []
    assert out["minimal_pair_chain"] is None
    assert out["candidate_versions"] == [] and out["approval_id"] is None


async def test_the_behaviour_comes_from_a_detector_set_over_the_runs_dataset(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(4)))
    # Another version of the SAME dataset (a detector set is usually built from a later version).
    from tests.support import db_factories

    with sync_session_factory()() as db:
        ds = db.get(Dataset, db.get(Version, version).dataset_id)  # type: ignore[union-attr]
        later = str(db_factories.version(db, ds=ds, number=2).id)
        db.commit()
    unrelated = make_version(version_table(texts(2, "other")))
    unkeyed = detector_set(later, key=None, meaning="harmful to someone", name="unkeyed-set")
    keyed = detector_set(later, key="high-stakes", meaning="real consequences", name="keyed-set")
    detector_set(later, key="archived-key", meaning="old", name="old-set", archived=True)
    detector_set(unrelated, key="other-key", meaning="not this run", name="other-set")
    run_id = await started(client, version)
    out = await get(client, run_id)
    assert out["behaviour"] == {
        "key": "high-stakes",
        "meaning": "real consequences",
        "source": "detector_set",
        "source_id": keyed,
        "source_name": "keyed-set",
    }
    # The keyed set first; an unkeyed set still says what a positive means; archived and unrelated
    # sets never appear.
    assert [b["source_id"] for b in out["behaviours"]] == [keyed, unkeyed]
    assert out["behaviours"][1]["key"] is None
    assert out["behaviours"][1]["meaning"] == "harmful to someone"


async def test_the_candidate_version_built_from_the_run_is_named(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver  # noqa: F811
) -> None:
    version = make_version(version_table(["q one", "q two", "q three"]))
    run_id = await started(client, version, sample_size=3)
    gen.run_until_done(run_id)
    candidate = await build_candidate(client, driver, run_id)
    out = await get(client, run_id)
    _, name, _ = dataset_of(candidate)
    (only,) = out["candidate_versions"]
    assert only["id"] == candidate and only["dataset_name"] == name
    with sync_session_factory()() as db:
        number = int(db.get(Version, candidate).number)  # type: ignore[union-attr]
    assert only["number"] == number


async def test_a_minimal_pair_chain_names_its_rubric_and_behaviour(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    version = make_version(version_table(texts(4)))
    created = await client.post(
        "/api/v1/rubrics", json={"name": "ctx/stakes", "body": RUBRIC, "behaviour_key": "pii"}
    )
    assert created.status_code == 201, created.text
    rubric_id = created.json()["id"]
    run_id = await started(client, version)
    with sync_session_factory()() as db:
        db.add(
            MinimalPairChain(
                id=new_id("mpc"),
                state="running",
                stage="generate",
                input_version_id=version,
                request={"rubric_id": rubric_id, "flip_from": "yes", "flip_to": "no"},
                generation_run_id=run_id,
                counts={},
                started_by="T",
                started_by_origin="operator",
                acting_by="T",
                acting_origin="operator",
            )
        )
        db.commit()
        chain_id = db.execute(
            select(MinimalPairChain.id).where(MinimalPairChain.generation_run_id == run_id)
        ).scalar_one()
    out = await get(client, run_id)
    assert out["minimal_pair_chain"] == {
        "id": chain_id,
        "state": "running",
        "stage": "generate",
        "rubric_id": rubric_id,
        "rubric_name": "ctx/stakes v1",
        "flip_from": "yes",
        "flip_to": "no",
        "pair_version": None,
    }
    assert out["behaviour"] == {
        "key": "pii",
        "meaning": None,
        "source": "rubric",
        "source_id": rubric_id,
        "source_name": "ctx/stakes v1",
    }


async def test_a_judge_listed_but_not_resident_reports_its_revision(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    """The 2026-10-21 reading defect: the judge's revision read only from the RESIDENT model, so a
    judge miLLM lists with a revision was recorded as "not reported"."""
    gen.fake.extra_models.append(
        {"id": 31, "name": JUDGE_MODEL, "revision": "0e9e39f249a16976918f6564b8830bc894c89659"}
    )
    version = make_version(version_table(texts(4)))
    run_id = await started(client, version)
    out = await get(client, run_id)
    assert out["judge_identity"]["model_id"] == JUDGE_MODEL
    assert out["judge_identity"]["revision"] == "0e9e39f249a16976918f6564b8830bc894c89659"
