"""Migration 0039 (017 slice 2) on a POPULATED database (017 Review R2).

``test_migrations_reverse.py`` round-trips the chain on an empty database. This file round-trips
0039 alone with rows in every table it touches: the older runs (a standard run and a minimal-pair
detector run), a recipe, a version and a detector set survive down to 0038 and back up with their
data; on the way up nothing is invented (no key, no definition, ``requests_in_flight`` 1); the
renamed detector CHECK (``detector_is_minimal_pairs`` -> ``detector_mode``) and the new
``carrier_shape`` CHECK refuse what they must at the database; the downgrade refuses while a
carrier run exists (dropping the modes would orphan it).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models.detector_set import DetectorSet
from src.models.generation import GenerationRun
from tests.support import db_factories

RUNS = "dw_generation_runs"


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _run(version_id: str, **over: Any) -> GenerationRun:
    fields: dict[str, Any] = {
        "id": new_id("gr"),
        "mode": "standard",
        "target_type": "sft",
        "input_version_id": version_id,
        "held_out_origin_version_id": version_id,
        "held_out_splits": ["test"],
        "prompt_column": "text",
        "seed_splits": ["train"],
        "sample_size": 10,
        "seed": 1,
        "n_responses": 1,
        "stages": ["respond"],
        "generation_endpoint": {"role": "generator"},
        "server_kind": "generic",
        "generator_identities": [],
        "engine_path": "native",
        "steering_supported": False,
        "state": "completed",
        "warnings": [],
        "counts": {},
        "started_by": "Test Operator",
        "started_by_origin": "operator",
    }
    fields.update(over)
    return GenerationRun(**fields)


def _checks() -> dict[str, str]:
    with get_sync_engine().connect() as conn:
        return {
            str(name): str(definition)
            for name, definition in conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'dw_generation_runs'::regclass AND contype = 'c'"
                )
            )
        }


def _columns(table: str) -> set[str]:
    with get_sync_engine().connect() as conn:
        return set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            ).scalars()
        )


@pytest.fixture
def populated(clean_db: None) -> Iterator[dict[str, str]]:
    with sync_session_factory()() as db:
        version = db_factories.version(db)
        recipe, _ = db_factories.recipe(db)
        standard = _run(str(version.id))
        minimal = _run(
            str(version.id), mode="minimal_pairs", target_type="detector", stages=["respond"]
        )
        db.add_all([standard, minimal])
        db.add(
            DetectorSet(
                id=new_id("ds"),
                name="migration-0039-set",
                positive_meaning="the text shows it",
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.commit()
        ids = {
            "version": str(version.id),
            "recipe": str(recipe.id),
            "standard": standard.id,
            "minimal": minimal.id,
        }
    yield ids
    command.upgrade(_config(), "head")


def test_0039_round_trips_a_populated_database(populated: dict[str, str]) -> None:
    config = _config()
    before = _snapshot(populated)
    try:
        command.downgrade(config, "0038")
        checks = _checks()
        assert "ck_dw_generation_runs_detector_is_minimal_pairs" in checks
        assert "ck_dw_generation_runs_detector_mode" not in checks
        assert "ck_dw_generation_runs_carrier_shape" not in checks
        assert "behaviour_key" not in _columns(RUNS) | _columns("dw_recipes")
        assert "behaviour_definition" not in _columns("dw_versions")
        with get_sync_engine().connect() as conn:
            modes = dict(conn.execute(text(f"SELECT id, mode FROM {RUNS}")).all())
            length = conn.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = 'mode'"
                ),
                {"t": RUNS},
            ).scalar_one()
        assert modes == {populated["standard"]: "standard", populated["minimal"]: "minimal_pairs"}
        assert length == 16
    finally:
        command.upgrade(config, "head")
    checks = _checks()
    assert "ck_dw_generation_runs_detector_is_minimal_pairs" not in checks
    assert "minimal_pairs" in checks["ck_dw_generation_runs_detector_mode"]
    assert "carrier_instructed" in checks["ck_dw_generation_runs_detector_mode"]
    assert "slot_token IS NOT NULL" in checks["ck_dw_generation_runs_carrier_shape"]
    after = _snapshot(populated)
    assert after["runs"] == before["runs"], "the older runs keep every value"
    assert after["counts"] == before["counts"]
    with get_sync_engine().connect() as conn:
        added = conn.execute(
            text(
                f"SELECT behaviour_key, generator_model_id, template_map, slot_token, "
                f"requests_in_flight, responds_to FROM {RUNS} ORDER BY id"
            )
        ).all()
        assert all(tuple(r) == (None, None, None, None, 1, None) for r in added), added
        assert conn.execute(
            text(
                "SELECT behaviour_definition, behaviour_configuration, behaviour_census "
                "FROM dw_versions WHERE id = :v"
            ),
            {"v": populated["version"]},
        ).one() == (None, None, None)
        assert conn.execute(text("SELECT behaviour_definition FROM dw_detector_sets")).all() == [
            (None,)
        ]
        keys = conn.execute(text("SELECT behaviour_key FROM dw_recipes")).scalars().all()
        assert keys and all(k is None for k in keys), keys


def _snapshot(ids: dict[str, str]) -> dict[str, Any]:
    columns = (
        "id, mode, target_type, input_version_id, held_out_splits, prompt_column, seed_splits, "
        "sample_size, seed, n_responses, stages, state, counts"
    )
    with get_sync_engine().connect() as conn:
        runs = [tuple(r) for r in conn.execute(text(f"SELECT {columns} FROM {RUNS} ORDER BY id"))]
        counts = {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in ("dw_versions", "dw_recipes", "dw_detector_sets", RUNS)
        }
    return {"runs": runs, "counts": counts}


def test_the_database_refuses_what_0039_forbids(populated: dict[str, str]) -> None:
    version = populated["version"]
    carrier = {
        "mode": "carrier_slot",
        "target_type": "detector",
        "generator_model_id": "gen-model",
        "template_map": {"column": "genre", "templates": {"a": "gt_1"}},
        "slot_token": "[[SLOT-0A1B2C3D]]",
    }
    refused: list[dict[str, Any]] = [
        {"mode": "standard", "target_type": "detector"},  # detector_mode
        {**carrier, "slot_token": None},  # a slot run without its token
        {**carrier, "mode": "carrier_instructed", "generator_model_id": None},
        {**carrier, "template_map": None},
        {**carrier, "n_responses": 2},
        {**carrier, "chosen_side": "a"},
        {**carrier, "target_type": "sft"},
        {"requests_in_flight": 0},
    ]
    accepted = []
    for over in refused:
        with sync_session_factory()() as db:
            db.add(_run(version, **over))
            try:
                db.commit()
            except IntegrityError:
                continue
            accepted.append(over)
    assert accepted == [], "the database accepted what 0039 forbids"
    with sync_session_factory()() as db:
        db.add(_run(version, **carrier))
        db.add(_run(version, **{**carrier, "mode": "carrier_instructed", "slot_token": None}))
        db.commit()


def test_the_downgrade_refuses_while_a_carrier_run_exists(populated: dict[str, str]) -> None:
    with sync_session_factory()() as db:
        db.add(
            _run(
                populated["version"],
                mode="carrier_instructed",
                target_type="detector",
                generator_model_id="gen-model",
                template_map={"column": "genre", "templates": {"a": "gt_1"}},
            )
        )
        db.commit()
    with pytest.raises(RuntimeError, match="carrier generation run"):
        command.downgrade(_config(), "0038")
    assert "carrier_shape" in " ".join(_checks()), "nothing was dropped"
