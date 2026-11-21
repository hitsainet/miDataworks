"""A version's sensitivity: the floor finalize stores, lowering, and the live raise (021 FTASKS
8.5 - 8.7; FR-021.18 - FR-021.20; suite P42).

Real builds over the stub operators (002's orchestrator, the real finalize). A source is marked as
session data through the operator's route, exactly as an operator does it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Job, Version
from src.services import sensitivity
from tests.support import db_factories
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TEST, HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]


async def build(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    inputs: list[dict[str, Any]],
    *,
    declared: str | None = None,
    name: str = "sens",
    headers: dict[str, str] | None = None,
    bindings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """The build's job: ``{"status", "version_id"?, "error"?}`` (or the HTTP answer when it
    does not queue)."""
    recipe = body(("stub_keep", {}))
    if declared is not None:
        recipe["sensitivity"] = declared
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "detector"})
    assert ds.status_code == 201, ds.text
    rec = await client.post("/api/v1/recipes", json={"name": f"{name}-r", "body": recipe})
    assert rec.status_code == 201, rec.text
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": inputs,
            "bindings": bindings or [],
        },
        headers=headers or {},
    )
    if response.status_code != 202 or "job_id" not in response.json():
        return {"status": "http", "code": response.status_code, "body": response.json()}
    status = driver.run(response.json()["job_id"])
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        return {"status": status, **job.result}


def source(data_dir: Path, tag: str) -> dict[str, str]:
    sid = make_source(data_dir, {"train": HUMOR_TRAIN, "test": HUMOR_TEST}, repo_id=f"t/{tag}")
    return {"kind": "source", "source_id": sid}


def version_input(version_id: str) -> dict[str, str]:
    return {"kind": "version", "version_id": version_id}


def stored(version_id: str) -> tuple[str | None, dict[str, Any] | None]:
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        return row.sensitivity, row.sensitivity_basis


def live(version_id: str) -> str | None:
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        return sensitivity.effective(db, row)[0]


async def ok(client: httpx.AsyncClient, driver: BuildDriver, inputs: list[Any], **kw: Any) -> str:
    out = await build(client, driver, inputs, **kw)
    assert out["status"] == "completed", out
    return str(out["version_id"])


async def test_floor_reduction(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """FR-021.18 (M-22, M-23 neighbourhood): a child of a sensitive parent is sensitive with no
    declaration; a child of a not-sensitive parent and an undeclared source is not declared; a
    child of a not-sensitive parent alone stays not sensitive."""
    hot = await ok(client, driver, [source(data_dir, "hot")], declared="sensitive", name="hot")
    assert stored(hot)[0] == "sensitive"
    child = await ok(client, driver, [version_input(hot)], name="hot-child")
    assert stored(child)[0] == "sensitive"
    basis = stored(child)[1]
    assert basis is not None and basis["declared"] is None
    assert [(i["version_id"], i["sensitivity"]) for i in basis["inputs"]] == [(hot, "sensitive")]
    cold = await ok(
        client, driver, [source(data_dir, "cold")], declared="not_sensitive", name="cold"
    )
    assert stored(cold)[0] == "not_sensitive"
    mixed = await ok(client, driver, [version_input(cold), source(data_dir, "raw")], name="mixed")
    assert stored(mixed)[0] is None
    alone = await ok(client, driver, [version_input(cold)], name="alone")
    assert stored(alone)[0] == "not_sensitive"


async def test_sensitivity_below_floor(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """FR-021.19 (M-23): not sensitive over a sensitive input, or over a source of recorded user
    traffic, is refused; over an undeclared source it is allowed (for the operator)."""
    hot = await ok(client, driver, [source(data_dir, "below")], declared="sensitive", name="below")
    out = await build(
        client, driver, [version_input(hot)], declared="not_sensitive", name="below-child"
    )
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "sensitivity_below_floor"
    assert out["error"]["details"]["inputs"][0]["version_id"] == hot
    traffic = source(data_dir, "traffic")
    marked = await client.post(
        f"/api/v1/sources/{traffic['source_id']}/session-data", json={"session_data": True}
    )
    assert marked.status_code == 200, marked.text
    out = await build(client, driver, [traffic], declared="not_sensitive", name="traffic")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "sensitivity_below_floor"
    assert out["error"]["details"]["inputs"] == [
        {"kind": "source", "source_id": traffic["source_id"], "sensitivity": "sensitive"}
    ]
    allowed = await ok(
        client, driver, [source(data_dir, "plain")], declared="not_sensitive", name="plain"
    )
    assert stored(allowed)[0] == "not_sensitive"


async def test_effective_raise(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """FR-021.20 (P42; M-24): a version with a null floor whose source is marked session data
    AFTER the build reads sensitive; so does its child; the stored column never changes."""
    raw = source(data_dir, "later")
    version_id = await ok(client, driver, [raw], name="later")
    child = await ok(client, driver, [version_input(version_id)], name="later-child")
    assert stored(version_id)[0] is None and live(version_id) is None
    marked = await client.post(
        f"/api/v1/sources/{raw['source_id']}/session-data", json={"session_data": True}
    )
    assert marked.status_code == 200, marked.text
    assert live(version_id) == "sensitive" and live(child) == "sensitive"
    assert stored(version_id)[0] is None
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        value, basis = sensitivity.effective(db, row)
    assert basis["raised_by"] == [{"kind": "session_data_source", "source_id": raw["source_id"]}]
    assert basis["floor"] is None
    # R2 review fix (OR2-021-2), 2026-11-16: the child's own manifest names the source, so the
    # walk lists it once, without a ``via``, not again through the parent.
    with sync_session_factory()() as db:
        child_row = db.get(Version, child)
        assert child_row is not None
        child_basis = sensitivity.effective(db, child_row)[1]
    assert child_basis["raised_by"] == [
        {"kind": "session_data_source", "source_id": raw["source_id"]}
    ]


def test_rule_source_ancestors_and_bindings_count(clean_db: None) -> None:
    """FR-021.18 (M-22): a rule-source ancestor's sensitivity enters the floor like an input's,
    and so does each binding's version; an unknown binding kind fails loudly."""
    import pytest

    with sync_session_factory()() as db:
        hot = db_factories.version(db, sensitivity="sensitive")
        db.flush()
        items = [{"kind": "rule_source", "version_id": hot.id}]
        value, basis = sensitivity.floor(db, {}, items)
        assert value == "sensitive"
        assert basis["inputs"][0]["via"] == "rule_source"
        with pytest.raises(LookupError, match="no sensitivity rule"):
            sensitivity.floor(db, {}, [], [{"kind": "probe_run", "id": "x"}])
        db.rollback()


async def test_the_revise_answer_names_dropped_declarations(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver
) -> None:
    """FR-021.30 (FTASKS 15.4; M-37): a revision that leaves out a recipe-level declaration the
    head had is saved (a note, never a refusal) and the answer names what was dropped."""
    declared = {
        **body(("stub_keep", {})),
        "visibility_floor": "private_only",
        "sensitivity": "sensitive",
    }
    rec = await client.post("/api/v1/recipes", json={"name": "dropping", "body": declared})
    assert rec.status_code == 201, rec.text
    rid = rec.json()["id"]
    kept = await client.post(f"/api/v1/recipes/{rid}/revisions", json={"body": declared})
    assert kept.status_code == 201 and kept.json()["dropped_declarations"] == []
    plain = body(("stub_keep", {}))
    dropped = await client.post(f"/api/v1/recipes/{rid}/revisions", json={"body": plain})
    assert dropped.status_code == 201, dropped.text
    assert dropped.json()["dropped_declarations"] == ["visibility_floor", "sensitivity"]
    again = await client.post(
        f"/api/v1/recipes/{rid}/revisions?view=summary", json={"body": declared}
    )
    assert again.json()["dropped_declarations"] == []


# --- R1 review fix (OR1-021-2, OR1-021-3), 2026-11-16: binding versions enter the floor ------------


def _generation_run(db: Any, input_version: str, held_out_origin: str) -> str:
    from src.core.ids import new_id
    from src.models.generation import GenerationRun

    run = GenerationRun(
        id=new_id("gr"),
        mode="standard",
        target_type="sft",
        input_version_id=input_version,
        held_out_origin_version_id=held_out_origin,
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
        started_by="Test Operator",
        started_by_origin="operator",
    )
    db.add(run)
    db.flush()
    return str(run.id)


@pytest.mark.parametrize("bound", ["label_run", "generation_input", "generation_held_out"])
def test_binding_versions_enter_the_floor(clean_db: None, bound: str) -> None:
    """FR-021.18 (OR1-021-2; R4): a label run's input version, a generation run's input version and
    its held-out origin version each raise the floor; not_sensitive over them is refused."""
    from src.services.assembly import BuildRefusal
    from tests.support.calibration_fixtures import make_run

    with sync_session_factory()() as db:
        hot = db_factories.version(db, sensitivity="sensitive")
        cold = db_factories.version(db, sensitivity="not_sensitive")
        db.commit()
        hot_id, cold_id = str(hot.id), str(cold.id)
    if bound == "label_run":
        binding = {"kind": "label_run", "id": make_run(hot_id, {}).id}
    with sync_session_factory()() as db:
        if bound == "generation_input":
            binding = {"kind": "generation_run", "id": _generation_run(db, hot_id, cold_id)}
        elif bound == "generation_held_out":
            binding = {"kind": "generation_run", "id": _generation_run(db, cold_id, hot_id)}
        value, basis = sensitivity.floor(db, {}, [], [binding])
        assert value == "sensitive", bound
        assert any(i["sensitivity"] == "sensitive" for i in basis["inputs"])
        with pytest.raises(BuildRefusal) as caught:
            sensitivity.floor(db, {"sensitivity": "not_sensitive"}, [], [binding])
        assert caught.value.code == "sensitivity_below_floor"
        db.rollback()


def test_a_bound_run_with_no_row_is_refused(clean_db: None) -> None:
    """OVN-wsp-dataworks-6 (OR1-021-3; R17): a run the resolver admitted that has no row now (a
    race) is refused, never counted as not declared."""
    from src.services.assembly import BuildRefusal

    with sync_session_factory()() as db:
        with pytest.raises(BuildRefusal) as caught:
            sensitivity.floor(db, {}, [], [{"kind": "label_run", "id": "lr_gone"}])
    assert caught.value.code == "binding_not_found"
    assert caught.value.details == {"kind": "label_run", "id": "lr_gone"}


async def test_a_build_bound_to_a_run_over_a_sensitive_version_is_sensitive(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OR1-021-2, through finalize: the build's bindings reach the floor (the finalize call passes
    ``request["bindings"]``)."""
    from tests.support.calibration_fixtures import make_run

    with sync_session_factory()() as db:
        hot = db_factories.version(db, sensitivity="sensitive")
        db.commit()
        hot_id = str(hot.id)
    run_id = make_run(hot_id, {}).id
    out = await build(
        client,
        driver,
        [source(data_dir, "bound")],
        name="bound",
        bindings=[{"kind": "label_run", "id": run_id}],
    )
    assert out["status"] == "completed", out
    assert stored(str(out["version_id"]))[0] == "sensitive"


def test_a_bound_versions_later_session_data_raises_the_live_value(clean_db: None) -> None:
    """R1 review fix (OR1-021-9): the live raise also follows the versions a build BOUND (a label
    or generation run's), whose sources a manifest's ``sources`` omit."""
    import hashlib

    from src.core.canonical_json import canonical_json
    from src.models import Source
    from tests.support.calibration_fixtures import make_run

    with sync_session_factory()() as db:
        src_row = db_factories.source(db, repo_id="org/traffic")
        manifest = canonical_json({"format": "test", "sources": [{"source_id": src_row.id}]})
        read = db_factories.version(
            db, manifest=manifest, manifest_sha256=hashlib.sha256(manifest).hexdigest()
        )
        db.commit()
        read_id, source_id = str(read.id), str(src_row.id)
    run_id = make_run(read_id, {}).id
    with sync_session_factory()() as db:
        bound = db_factories.version(db, bindings=[{"kind": "label_run", "id": run_id}])
        db.commit()
        assert sensitivity.effective(db, bound)[0] is None
        row = db.get(Source, source_id)
        assert row is not None
        row.session_data, row.session_data_basis = True, "operator"
        db.commit()
        value, basis = sensitivity.effective(db, db.get(Version, bound.id))
    assert value == "sensitive"
    assert basis["raised_by"] == [
        {"kind": "session_data_source", "source_id": source_id, "via": f"label_run:{run_id}"}
    ]


# --- R2 review fix (OR2-021-2, OR2-021-6), 2026-11-16: the live raise walks the lineage closure ---


async def test_a_child_of_a_bound_version_is_raised(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """OR2-021-2 (the review's probe): P bound a label run over B; V is built FROM P. B's source
    becomes session data after V was built. B, P and V all read sensitive, and V's basis names the
    binding the data came through."""
    from tests.support.calibration_fixtures import make_run

    b_src = source(data_dir, "cb-b")
    b = await ok(client, driver, [b_src], name="cb-b")
    run_id = make_run(b, {}).id
    p = await ok(
        client,
        driver,
        [source(data_dir, "cb-p")],
        name="cb-p",
        bindings=[{"kind": "label_run", "id": run_id}],
    )
    v = await ok(client, driver, [version_input(p)], name="cb-v")
    assert (live(b), live(p), live(v)) == (None, None, None)
    marked = await client.post(
        f"/api/v1/sources/{b_src['source_id']}/session-data", json={"session_data": True}
    )
    assert marked.status_code == 200, marked.text
    assert (live(b), live(p), live(v)) == ("sensitive", "sensitive", "sensitive")
    assert stored(v)[0] is None
    with sync_session_factory()() as db:
        row = db.get(Version, v)
        assert row is not None
        basis = sensitivity.effective(db, row)[1]
    assert basis["raised_by"] == [
        {
            "kind": "session_data_source",
            "source_id": b_src["source_id"],
            "via": f"label_run:{run_id}",
        }
    ]


def _flag(source_id: str) -> None:
    from src.models import Source

    with sync_session_factory()() as db:
        row = db.get(Source, source_id)
        assert row is not None
        row.session_data, row.session_data_basis = True, "operator"
        db.commit()


def _sourced_version(db: Any, repo_id: str) -> tuple[Any, str]:
    import hashlib

    from src.core.canonical_json import canonical_json

    src_row = db_factories.source(db, repo_id=repo_id)
    manifest = canonical_json({"format": "test", "sources": [{"source_id": src_row.id}]})
    row = db_factories.version(
        db, manifest=manifest, manifest_sha256=hashlib.sha256(manifest).hexdigest()
    )
    return row, str(src_row.id)


def test_a_deep_chain_is_walked_without_recursion(clean_db: None) -> None:
    """OR2-021-6: 1100 levels (binding at the top, version inputs below) end in a source that
    becomes session data; the tip reads sensitive and the walk does not exhaust the stack."""
    from src.models.version import VersionInput
    from tests.support.calibration_fixtures import make_run

    depth = 1100
    with sync_session_factory()() as db:
        root, source_id = _sourced_version(db, "org/deep")
        chain = [str(root.id)]
        for _ in range(depth):
            nxt = db_factories.version(db)
            db.add(
                VersionInput(
                    version_id=nxt.id, position=0, kind="version", input_version_id=chain[-1]
                )
            )
            chain.append(str(nxt.id))
        db.commit()
    run_id = make_run(chain[-1], {}).id
    with sync_session_factory()() as db:
        tip = db_factories.version(db, bindings=[{"kind": "label_run", "id": run_id}])
        db.commit()
        tip_id = str(tip.id)
    _flag(source_id)
    with sync_session_factory()() as db:
        value, basis = sensitivity.effective(db, db.get(Version, tip_id))
    assert value == "sensitive"
    assert basis["raised_by"] == [
        {"kind": "session_data_source", "source_id": source_id, "via": f"version:{chain[0]}"}
    ]


def test_a_cycle_in_the_lineage_ends(clean_db: None) -> None:
    """OR2-021-6: a lineage that loops (v1 reads v2 as an input row; v2 bound a run over v1)
    terminates, and both read sensitive once v1's source is session data."""
    from src.models.version import VersionInput
    from tests.support.calibration_fixtures import make_run

    with sync_session_factory()() as db:
        v1, source_id = _sourced_version(db, "org/cycle")
        db.commit()
        v1_id = str(v1.id)
    run_id = make_run(v1_id, {}).id
    with sync_session_factory()() as db:
        v2 = db_factories.version(db, bindings=[{"kind": "label_run", "id": run_id}])
        db.add(VersionInput(version_id=v1_id, position=0, kind="version", input_version_id=v2.id))
        db.commit()
        v2_id = str(v2.id)
    with sync_session_factory()() as db:
        assert sensitivity.effective(db, db.get(Version, v1_id))[0] is None
        assert sensitivity.effective(db, db.get(Version, v2_id))[0] is None
    _flag(source_id)
    with sync_session_factory()() as db:
        assert sensitivity.effective(db, db.get(Version, v1_id))[0] == "sensitive"
        value, basis = sensitivity.effective(db, db.get(Version, v2_id))
    assert value == "sensitive"
    assert basis["raised_by"] == [
        {"kind": "session_data_source", "source_id": source_id, "via": f"label_run:{run_id}"}
    ]


# --- R3 review fix (OR3-021-2), 2026-11-16: the closure's held-out-origin and rule-source edges -----


def _completed_generation_run(input_id: str, held_out_id: str) -> str:
    from src.core.ids import new_id
    from src.models.generation import GenerationRun

    with sync_session_factory()() as db:
        run = GenerationRun(
            id=new_id("gr"),
            mode="standard",
            target_type="sft",
            input_version_id=input_id,
            held_out_origin_version_id=held_out_id,
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
        return str(run.id)


def _version_over_one_source(repo_id: str) -> tuple[str, str]:
    with sync_session_factory()() as db:
        row, source_id = _sourced_version(db, repo_id)
        db.commit()
        return str(row.id), source_id


def _live_of(version_id: str) -> str | None:
    with sync_session_factory()() as db:
        return sensitivity.effective(db, db.get(Version, version_id))[0]


def test_a_bound_generation_runs_held_out_origin_raises(clean_db: None) -> None:
    """A generation run whose input is clean and whose held-out origin's source is flagged after
    the build: the version that bound the run reads sensitive."""
    clean, _ = _version_over_one_source("org/ho-clean")
    held, held_source = _version_over_one_source("org/ho-held")
    run_id = _completed_generation_run(clean, held)
    with sync_session_factory()() as db:
        bound = db_factories.version(db, bindings=[{"kind": "generation_run", "id": run_id}])
        db.commit()
        bound_id = str(bound.id)
    assert _live_of(bound_id) is None
    _flag(held_source)
    assert _live_of(bound_id) == "sensitive", "a bound generation run's held-out origin is missed"


def test_a_rule_source_ancestors_binding_raises(clean_db: None) -> None:
    """V's rule-source ancestor R bound a label run over B; B's source is flagged after the build.
    R's own sources reach V's manifest, its bindings do not: the walk must follow the edge."""
    from src.models.version import VersionInput
    from tests.support.calibration_fixtures import make_run

    b, b_source = _version_over_one_source("org/rs-b")
    run_id = make_run(b, {}).id
    with sync_session_factory()() as db:
        r = db_factories.version(db, bindings=[{"kind": "label_run", "id": run_id}])
        db.commit()
        r_id = str(r.id)
    with sync_session_factory()() as db:
        v = db_factories.version(db, inputs=[{"kind": "rule_source", "version_id": r_id}])
        db.add(VersionInput(version_id=v.id, position=0, kind="rule_source", input_version_id=r_id))
        db.commit()
        v_id = str(v.id)
    assert _live_of(v_id) is None
    _flag(b_source)
    assert _live_of(r_id) == "sensitive"
    assert _live_of(v_id) == "sensitive", "a rule-source ancestor's binding is missed"


# --- R3 review fix (OR3-021-5), 2026-11-16: one lineage walk per read ------------------------------


async def test_a_detail_read_and_check_assembly_walk_the_lineage_once(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /versions/{id}`` and publish-check assembly each call ``effective`` once, on this
    version, and pass its value on (``published_below``, ``implied_floor``); the answers are the
    live ones."""
    from src.services.publishing.check_inputs import assemble

    raw = source(data_dir, "once")
    version_id = await ok(client, driver, [raw], name="once")
    marked = await client.post(
        f"/api/v1/sources/{raw['source_id']}/session-data", json={"session_data": True}
    )
    assert marked.status_code == 200, marked.text
    seen: list[str] = []
    real = sensitivity.effective

    def spy(session: Any, version: Any) -> Any:
        seen.append(str(version.id))
        return real(session, version)

    monkeypatch.setattr(sensitivity, "effective", spy)
    read = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert read["sensitivity"] == "sensitive"
    assert seen == [version_id]
    seen.clear()
    with sync_session_factory()() as db:
        row = db.get(Version, version_id)
        assert row is not None
        inputs = assemble(
            db, row, label_column="label", visibility="private", token_scope="write"  # noqa: S106
        ).inputs
    assert (inputs.sensitivity, inputs.visibility_floor) == ("sensitive", "private_only")
    assert seen == [version_id]
