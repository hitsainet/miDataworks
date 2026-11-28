"""An agent's lowering of sensitivity waits for the operator, on every build path (021 FTASKS 12.x;
FR-021.22; suite P42; R1A-D3, R1A-D18, R2B-13, R1B-X13).

The authority is ``version_build_service.request_build``: every version build passes through it.
The two version routes also queue the approval up front (the 202), and finalize re-checks.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from src.core.agent_origin import Actor, Who
from src.core.database import sync_session_factory
from src.models import Job, Version
from src.models.approval import Approval
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TEST, HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]

AGENT = {"X-Dataworks-Agent": "agent:mcp"}
SRC = Path(__file__).resolve().parents[2] / "src"


def count(model: Any) -> int:
    with sync_session_factory()() as db:
        return int(db.execute(select(func.count()).select_from(model)).scalar_one())


async def lowering_fixture(
    client: httpx.AsyncClient, data_dir: Path, name: str = "lower"
) -> dict[str, Any]:
    """A recipe declaring ``not_sensitive`` over a source nothing declares: a lowering."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST}, repo_id=f"t/{name}")
    recipe = {**body(("stub_keep", {})), "sensitivity": "not_sensitive"}
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": recipe})
    assert ds.status_code == 201 and rec.status_code == 201, (ds.text, rec.text)
    return {
        "dataset_id": ds.json()["id"],
        "recipe_id": rec.json()["id"],
        "recipe_revision_id": rec.json()["head_revision_id"],
        "inputs": [{"kind": "source", "source_id": source}],
    }


# --- the per-caller list (AST, keyed by defining module, resolving aliases) ------------------------

#: Every call of the two build entry points under ``src/`` (R2B-13, R1B-X13). A new caller fails
#: here until it is listed AND driven by an agent test below (or shown to pass through these).
EXPECTED_CALLERS = {
    ("services.version_build_service.request_build", "api/v1/endpoints/_builds.py"),
    (
        "services.version_build_service.request_build",
        "services/detector_sets/minimal_pair_chain.py",
    ),
    ("api.v1.endpoints._builds.respond", "api/v1/endpoints/versions.py"),
    ("api.v1.endpoints._builds.respond", "api/v1/endpoints/recipes.py"),
    ("api.v1.endpoints._builds.respond", "api/v1/endpoints/generation.py"),
}
TARGETS = {
    "services.version_build_service": "request_build",
    "api.v1.endpoints._builds": "respond",
}


def _module_of(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _resolve(level: int, name: str | None, here: str) -> str:
    if level == 0:
        return (name or "").removeprefix("src.")
    base = here.split(".")[:-level]
    return ".".join([*base, *(name.split(".") if name else [])])


def build_callers(key_by_bare_name: bool = False) -> set[tuple[str, str]]:
    """``{(defining_module.function, caller_file)}``: each call resolved through the file's import
    table (``from X import f as g``, ``from . import mod`` then ``mod.f``), never by bare name."""
    found: set[tuple[str, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        here = _module_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: dict[str, str] = {}  # local name -> qualified target
        modules: dict[str, str] = {}  # local name -> module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                origin = _resolve(node.level, node.module, here)
                for alias in node.names:
                    local = alias.asname or alias.name
                    names[local] = f"{origin}.{alias.name}"
                    modules[local] = f"{origin}.{alias.name}"
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            qualified = None
            if isinstance(node.func, ast.Name):
                if key_by_bare_name and node.func.id in TARGETS.values():
                    qualified = f"?.{node.func.id}"
                else:
                    qualified = names.get(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if key_by_bare_name and node.func.attr in TARGETS.values():
                    qualified = f"?.{node.func.attr}"
                else:
                    mod = modules.get(node.func.value.id)
                    qualified = f"{mod}.{node.func.attr}" if mod else None
            if qualified is None:
                continue
            module, _, func_name = qualified.rpartition(".")
            if TARGETS.get(module) == func_name or (key_by_bare_name and module == "?"):
                found.add((qualified, str(path.relative_to(SRC))))
    return found


def test_agent_lowering_every_caller() -> None:
    """R2B-13, R1B-X13 (M-45): the callers of ``request_build`` and ``respond`` by their DEFINING
    module (candidate-build's ``build_respond`` is ``respond``; the publish build's own
    ``request_build`` is another function and is not listed)."""
    assert build_callers() == EXPECTED_CALLERS
    publish = ("services.publishing.build_service.request_build", "api/v1/endpoints/publishing.py")
    assert publish not in build_callers()


# --- request_build is the authority ----------------------------------------------------------------


async def _request_as_agent(fixture: dict[str, Any], approval_id: str | None = None) -> Any:
    from src.core.database import async_session_factory
    from src.schemas.versions import VersionBuildRequest
    from src.services import version_build_service

    req = VersionBuildRequest(
        dataset_id=fixture["dataset_id"],
        recipe_revision_id=fixture["recipe_revision_id"],
        inputs=fixture["inputs"],
        seed=5,
    )
    async with async_session_factory()() as db:
        return await version_build_service.request_build(
            db,
            req,
            Who(who="agent:mcp", origin="agent"),
            Actor(origin="agent", agent="agent:mcp", approval_id=approval_id),
        )


async def test_request_build_refuses_an_agents_lowering(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """The path with no decorator (candidate-build, the minimal-pair chain, any future caller)
    reaches here: refused by name, naming the inputs and the tool that waits (M-43)."""
    from src.core.errors import AppError

    fixture = await lowering_fixture(client, data_dir)
    jobs = count(Job)
    with pytest.raises(AppError) as caught:
        await _request_as_agent(fixture)
    assert caught.value.code == "sensitivity_declare_needs_approval"
    assert caught.value.status_code == 409
    assert caught.value.details["next"]["tool"] == "dataworks_build_version"
    assert [i["source_id"] for i in caught.value.details["inputs"]] == [
        fixture["inputs"][0]["source_id"]
    ]
    assert count(Job) == jobs  # nothing built
    # an approval for ANOTHER action, or not executing, does not count
    with pytest.raises(AppError):
        await _request_as_agent(fixture, approval_id="apr_does_not_exist")


# --- both routes queue the approval up front; the approved replay builds --------------------------


@pytest.mark.parametrize("route", ["versions", "recipe_build"])
async def test_agent_lowering_waits(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    route: str,
) -> None:
    """FR-021.22 (M-32, M-38): an agent's call answers 202 with ``sensitivity_declare`` naming the
    inputs, builds nothing; after the operator approves, the replay builds a not-sensitive
    version and its job records the approval."""
    fixture = await lowering_fixture(client, data_dir, f"wait-{route.replace('_', '-')}")
    payload = {k: fixture[k] for k in ("dataset_id", "recipe_revision_id", "inputs")}
    if route == "versions":
        response = await client.post("/api/v1/versions", json=payload, headers=AGENT)
    else:
        response = await client.post(
            f"/api/v1/recipes/{fixture['recipe_id']}/build",
            json={k: payload[k] for k in ("dataset_id", "inputs")},
            headers=AGENT,
        )
    assert response.status_code == 202, response.text
    pending = response.json()
    assert pending["action"] == "sensitivity_declare", pending
    assert count(Version) == 0
    with sync_session_factory()() as db:
        row = db.get(Approval, pending["approval_id"])
        assert row is not None
        assert row.payload["lowered"][0]["source_id"] == fixture["inputs"][0]["source_id"]
        assert len(row.payload["sensitivity_digest"]) == 64
    approved = await client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
    assert approved.json()["status"] == "executed", approved.text
    with sync_session_factory()() as db:
        job = db.execute(
            select(Job).where(Job.kind == "version_build", Job.started_by_origin == "agent")
        ).scalar_one()
        assert job.params["sensitivity_approval_id"] == pending["approval_id"]
        job_id = job.id
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        version = db.execute(select(Version)).scalar_one()
        assert version.sensitivity == "not_sensitive"


async def test_an_operator_lowering_runs(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    fixture = await lowering_fixture(client, data_dir, "operator")
    payload = {k: fixture[k] for k in ("dataset_id", "recipe_revision_id", "inputs")}
    response = await client.post("/api/v1/versions", json=payload)
    assert response.status_code == 202 and "job_id" in response.json(), response.text
    assert driver.run(response.json()["job_id"]) == "completed"


async def test_the_route_predicate_fails_closed(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """R1A-D18 (M-33): a lowering whose lookup fails (here a binding that does not exist) is
    gated, never run."""
    fixture = await lowering_fixture(client, data_dir, "closed")
    payload = {
        **{k: fixture[k] for k in ("dataset_id", "recipe_revision_id", "inputs")},
        "bindings": [{"kind": "label_run", "id": "lbr_doesnotexist0000000000000"}],
    }
    response = await client.post("/api/v1/versions", json=payload, headers=AGENT)
    assert response.status_code == 202, response.text
    assert response.json()["action"] == "sensitivity_declare"


async def test_finalize_rechecks_an_agent_job_without_an_approval(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """Defence in depth: an agent-started job that would lower, with no approval id in its params
    (a future caller that skipped ``request_build``), is refused at finalize."""
    fixture = await lowering_fixture(client, data_dir, "recheck")
    payload = {k: fixture[k] for k in ("dataset_id", "recipe_revision_id", "inputs")}
    response = await client.post("/api/v1/versions", json=payload)  # the operator queues it
    job_id = response.json()["job_id"]
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None
        job.started_by_origin = "agent"  # as if an agent's path had bypassed request_build
        db.commit()
    assert driver.run(job_id) == "failed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        assert job.result["error"]["code"] == "sensitivity_declare_needs_approval"
    assert count(Version) == 0


# --- R1 review fix (OR1-021-1), 2026-11-16: the approval binds THIS build --------------------------


def _approval(action: str, status: str, digest: str | None) -> str:
    from datetime import UTC, datetime, timedelta

    from src.core.ids import new_id

    with sync_session_factory()() as db:
        row = Approval(
            id=new_id("apr"),
            action=action,
            target="POST /api/v1/versions",
            summary=action,
            payload={"sensitivity_digest": digest, "lowered": []},
            request_digest="0" * 64,
            requested_by="agent:mcp",
            status=status,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(row)
        db.commit()
        return row.id


async def _digest(fixture: dict[str, Any]) -> str:
    from src.core.database import async_session_factory
    from src.schemas.versions import VersionBuildRequest
    from src.services import version_build_service

    req = VersionBuildRequest(
        dataset_id=fixture["dataset_id"],
        recipe_revision_id=fixture["recipe_revision_id"],
        inputs=fixture["inputs"],
        seed=5,
    )
    async with async_session_factory()() as db:
        facts = await version_build_service.sensitivity_facts_for_route(db, {"body": req})
    assert facts["lowered"] and facts["sensitivity_digest"]
    return str(facts["sensitivity_digest"])


@pytest.mark.parametrize(
    ("action", "status", "digest_of", "allowed"),
    [
        ("sensitivity_declare", "executing", "this", True),
        ("agent_label_rows", "executing", "this", False),  # R2: another action
        ("sensitivity_declare", "pending", "this", False),  # R3: not executing
        ("sensitivity_declare", "executed", "this", False),  # R3: already used
        ("sensitivity_declare", "executing", "other", False),  # R1: another build
    ],
    ids=["approved", "other_action", "pending", "executed", "other_digest"],
)
async def test_only_the_executing_approval_for_this_build_counts(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    action: str,
    status: str,
    digest_of: str,
    allowed: bool,
) -> None:
    """OR1-021-1: an approval of another action, one not executing now, or one whose digest names
    other inputs never lets an agent lower sensitivity."""
    from src.core.errors import AppError

    fixture = await lowering_fixture(client, data_dir, f"bind-{digest_of}-{status}")
    if digest_of == "this":
        digest = await _digest(fixture)
    else:
        other = await lowering_fixture(client, data_dir, f"other-{status}")
        digest = await _digest(other)
    approval_id = _approval(action, status, digest)
    if allowed:
        outcome = await _request_as_agent(fixture, approval_id=approval_id)
        assert outcome.kind == "started"
        with sync_session_factory()() as db:
            job = db.get(Job, outcome.job_id)
            assert job is not None and job.params["sensitivity_approval_id"] == approval_id
        return
    jobs = count(Job)
    with pytest.raises(AppError) as caught:
        await _request_as_agent(fixture, approval_id=approval_id)
    assert caught.value.code == "sensitivity_declare_needs_approval"
    assert count(Job) == jobs


async def test_a_head_revised_after_queueing_is_not_built_on_the_approval(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """OR1-021-1 (d): the recipe route builds the HEAD; an agent queues, then revises the head (the
    revision route is ungated); the operator approves what was shown; the replay is refused."""
    fixture = await lowering_fixture(client, data_dir, "revised")
    queued = await client.post(
        f"/api/v1/recipes/{fixture['recipe_id']}/build",
        json={"dataset_id": fixture["dataset_id"], "inputs": fixture["inputs"]},
        headers=AGENT,
    )
    assert queued.status_code == 202 and queued.json()["action"] == "sensitivity_declare"
    revised = {**body(("stub_keep", {}), ("stub_keep", {})), "sensitivity": "not_sensitive"}
    saved = await client.post(
        f"/api/v1/recipes/{fixture['recipe_id']}/revisions", json={"body": revised}, headers=AGENT
    )
    assert saved.status_code == 201, saved.text
    approved = await client.post(f"/api/v1/approvals/{queued.json()['approval_id']}/approve")
    assert approved.json()["status"] == "failed", approved.text
    # R2 review fix (OR2-021-1), 2026-11-16: the moved head is refused before the digest check.
    assert approved.json()["error"]["code"] == "recipe_head_moved"
    assert count(Version) == 0


# --- R2 review fix (OR2-021-1), 2026-11-16: an approval binds the head revision the card showed ----

MANY = [
    {"text": f"row number {i} of the gate fixture", "label": i % 2, "score": 0.1, "note": None}
    for i in range(6000)
]


async def _queue_on_recipe(
    client: httpx.AsyncClient, data_dir: Path, recipe: dict[str, Any], name: str, query: str = ""
) -> dict[str, Any]:
    """A dataset, a recipe and an agent's ``POST /recipes/{id}/build`` over 6000 source rows."""
    source = make_source(data_dir, {"train": MANY}, repo_id=f"t/{name}")
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": recipe})
    assert ds.status_code == 201 and rec.status_code == 201, (ds.text, rec.text)
    queued = await client.post(
        f"/api/v1/recipes/{rec.json()['id']}/build{query}",
        json={
            "dataset_id": ds.json()["id"],
            "inputs": [{"kind": "source", "source_id": source}],
            "seed": 3,
        },
        headers=AGENT,
    )
    assert queued.status_code == 202, queued.text
    return {
        "recipe_id": rec.json()["id"],
        "head": rec.json()["head_revision_id"],
        "approval_id": queued.json()["approval_id"],
        "action": queued.json()["action"],
    }


async def _revise(client: httpx.AsyncClient, recipe_id: str, recipe: dict[str, Any]) -> None:
    saved = await client.post(
        f"/api/v1/recipes/{recipe_id}/revisions", json={"body": recipe}, headers=AGENT
    )
    assert saved.status_code == 201, saved.text


def _ledger_and_jobs() -> tuple[list[tuple[str, int]], int]:
    from src.models import AgentLabelRow

    with sync_session_factory()() as db:
        rows = [(r.run_kind, r.rows_counted) for r in db.query(AgentLabelRow).all()]
        jobs = db.query(Job).filter(Job.kind == "version_build").count()
    return rows, jobs


async def test_a_sensitivity_approval_never_runs_a_labelling_head_revised_after_it(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """The review's scenario: an agent queues a small lowering build (``sensitivity_declare``),
    revises the head to a 6000-row labelling recipe with no declaration, and the operator approves
    what the card showed. The replay is refused and nothing is labelled."""
    queued = await _queue_on_recipe(
        client,
        data_dir,
        {**body(("stub_keep", {})), "sensitivity": "not_sensitive"},
        "swap-declare",
    )
    assert queued["action"] == "sensitivity_declare"
    await _revise(client, queued["recipe_id"], body(("stub_probe_verdict", {})))
    approved = await client.post(f"/api/v1/approvals/{queued['approval_id']}/approve")
    assert approved.json()["status"] == "failed", approved.text
    assert approved.json()["error"]["code"] == "recipe_head_moved"
    assert "head revision changed" in approved.json()["error"]["message"]
    rows, jobs = _ledger_and_jobs()
    assert [r for r in rows if r[0] == "probe_verdict"] == []
    assert jobs == 0 and count(Version) == 0


async def test_a_label_approval_of_n_rows_never_runs_a_head_labelling_more(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """The other way round: ``agent_label_rows`` approved for one labelling step; the head is
    revised to two. Refused; the ledger records nothing."""
    queued = await _queue_on_recipe(
        client, data_dir, body(("stub_probe_verdict", {})), "swap-label"
    )
    assert queued["action"] == "agent_label_rows"
    await _revise(
        client, queued["recipe_id"], body(("stub_probe_verdict", {}), ("stub_probe_verdict", {}))
    )
    approved = await client.post(f"/api/v1/approvals/{queued['approval_id']}/approve")
    assert approved.json()["status"] == "failed", approved.text
    assert approved.json()["error"]["code"] == "recipe_head_moved"
    assert _ledger_and_jobs() == ([], 0)


async def test_an_approval_over_an_unchanged_head_builds_it(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """The positive side: with the head unchanged the approved build runs once, and the stored
    request names the head the card showed, whatever the agent passed in the hidden parameter."""
    queued = await _queue_on_recipe(
        client,
        data_dir,
        body(("stub_probe_verdict", {})),
        "swap-none",
        query="?expected_head_revision_id=forged-by-the-agent",
    )
    with sync_session_factory()() as db:
        stored = db.get(Approval, queued["approval_id"])
        assert stored is not None
        assert stored.payload["expected_head_revision_id"] == queued["head"]
    approved = await client.post(f"/api/v1/approvals/{queued['approval_id']}/approve")
    assert approved.json()["status"] == "executed", approved.text
    rows, jobs = _ledger_and_jobs()
    assert rows == [("probe_verdict", 6000)] and jobs == 1


# --- R1 review fix (OR1-021-6), 2026-11-16: the two undecorated callers, driven as an agent --------


def _completed_generation_run(version_id: str) -> str:
    from src.core.ids import new_id
    from src.models.generation import GenerationRun

    with sync_session_factory()() as db:
        run = GenerationRun(
            id=new_id("gr"),
            mode="standard",
            target_type="sft",
            input_version_id=version_id,
            held_out_origin_version_id=version_id,
            held_out_splits=["test"],
            prompt_column="text",
            seed_splits=["train"],
            sample_size=10,
            seed=1,
            n_responses=1,
            stages=["respond"],
            generation_endpoint={"role": "generator"},
            server_kind="generic",
            generator_identities=[],
            engine_path="native",
            steering_supported=False,
            state="completed",
            warnings=[],
            counts={},
            started_by="agent:mcp",
            started_by_origin="agent",
        )
        db.add(run)
        db.commit()
        return run.id


async def _built_input(client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path) -> str:
    fixture = await lowering_fixture(client, data_dir, "input")
    plain = await client.post(
        "/api/v1/recipes", json={"name": "plain-r", "body": body(("stub_keep", {}))}
    )
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": fixture["dataset_id"],
            "recipe_revision_id": plain.json()["head_revision_id"],
            "inputs": fixture["inputs"],
        },
    )
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        return str(db.execute(select(Version.id)).scalar_one())


async def test_an_agents_candidate_build_over_a_not_sensitive_system_recipe_is_refused(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """OR1-021-6: candidate-build has no route decorator. The route reuses the recipe named for the
    run's candidate, so one already saved under that name declaring not_sensitive would lower; an
    agent's call is refused by request_build with nothing built (M-43 also turns this red)."""
    version_id = await _built_input(client, driver, data_dir)
    run_id = _completed_generation_run(version_id)
    name = f"generation-candidate-{run_id}"
    recipe = {**body(("stub_keep", {})), "sensitivity": "not_sensitive"}
    saved = await client.post("/api/v1/recipes", json={"name": name, "body": recipe})
    assert saved.status_code == 201, saved.text
    jobs = count(Job)
    refused = await client.post(
        f"/api/v1/generation-runs/{run_id}/candidate-build", json={"seed": 3}, headers=AGENT
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "sensitivity_declare_needs_approval"
    assert count(Job) == jobs


async def test_an_agent_started_minimal_pair_chain_build_is_refused(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """OR1-021-6: the minimal-pair chain's build stage (``_build``, acting as the agent that
    started the chain) over a system recipe declaring not_sensitive is refused, nothing built."""
    from types import SimpleNamespace

    from src.core.database import async_session_factory
    from src.core.errors import AppError
    from src.services.detector_sets import minimal_pair_chain

    version_id = await _built_input(client, driver, data_dir)
    run_id = _completed_generation_run(version_id)
    recipe = {**body(("stub_keep", {})), "sensitivity": "not_sensitive"}
    saved = await client.post("/api/v1/recipes", json={"name": "chain-stage-r", "body": recipe})
    assert saved.status_code == 201, saved.text
    chain = SimpleNamespace(acting_origin="agent", acting_by="agent:mcp", generation_run_id=run_id)
    jobs = count(Job)
    async with async_session_factory()() as db:
        with pytest.raises(AppError) as caught:
            await minimal_pair_chain._build(
                db,
                chain,  # type: ignore[arg-type]
                recipe_name="chain-stage-r",
                steps=[],
                labels=[],
                input_version_id=version_id,
                bindings=[],
            )
    assert caught.value.code == "sensitivity_declare_needs_approval"
    assert count(Job) == jobs


# --- R3 review fix (OR3-021-1, OR3-021-4), 2026-11-16: an approval with no recorded head -----------


def _strip_head(approval_id: str) -> None:
    """Make the stored approval look like one queued before heads were recorded (OVN-7): no
    ``expected_head_revision_id``, and a digest consistent with that payload, as such a row has."""
    from src.core.canonical_json import canonical_sha256

    with sync_session_factory()() as db:
        row = db.get(Approval, approval_id)
        assert row is not None
        payload = dict(row.payload)
        payload.pop("expected_head_revision_id")
        row.payload = payload
        row.request_digest = canonical_sha256(payload)
        db.commit()


@pytest.mark.parametrize(
    "recipe",
    [
        pytest.param(body(("stub_probe_verdict", {})), id="agent_label_rows"),
        pytest.param(
            {**body(("stub_keep", {})), "sensitivity": "not_sensitive"}, id="sensitivity_declare"
        ),
    ],
)
async def test_an_approval_with_no_recorded_head_is_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    recipe: dict[str, Any],
) -> None:
    """OVN-7's fail-closed branch: an approval that recorded no head (queued before the change, or
    the recipe unreadable when filed) is refused even with the head UNCHANGED, nothing is built,
    and the message says why, without claiming the head changed (OR3-021-4)."""
    queued = await _queue_on_recipe(client, data_dir, recipe, f"legacy-{len(recipe)}")
    _strip_head(queued["approval_id"])
    approved = await client.post(f"/api/v1/approvals/{queued['approval_id']}/approve")
    assert approved.json()["status"] == "failed", approved.text
    error = approved.json()["error"]
    assert error["code"] == "recipe_head_moved"
    assert "queued before miDataworks recorded" in error["message"]
    assert "changed" not in error["message"]
    assert _ledger_and_jobs() == ([], 0)
    assert count(Version) == 0
