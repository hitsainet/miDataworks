"""Migration 0042 (017 slice 7, FTASKS 10.1) on a POPULATED database, with its data step.

A detector set whose role binds a version with a contamination report takes that report's
``benchmarks[].source_id`` into ``benchmark_source_ids`` (the newest report wins); a set with no
report keeps ``[]``; versions keep ``visibility_floor`` NULL and sends ``send_group_id`` NULL
(nothing invented). The database refuses a floor other than ``private_only`` and a benchmark list
that is not an array; the downgrade refuses while a floor, a named benchmark or a group exists.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.models.curation import VersionReport
from src.models.detector_set import DetectorSet, DetectorSetRole
from src.models.version import Version
from tests.support import db_factories


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _report(version_id: str, sources: list[str] | None, at: datetime) -> None:
    with sync_session_factory()() as db:
        db.add(
            VersionReport(
                id=str(uuid.uuid4()),
                version_id=version_id,
                kind="contamination",
                operator_name="contamination_check",
                operator_version="1",
                manifest_hash="a" * 64,
                params_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                params={},
                inputs=[{"version_id": version_id}],
                inputs_digest="b" * 64,
                seed=1,
                artefacts=[],
                state="completed",
                result={
                    "benchmarks": (
                        None
                        if sources is None
                        else [{"source_id": s, "rows_overlapping": 0} for s in sources]
                    )
                },
                started_by="Test Operator",
                started_by_origin="operator",
                created_at=at,
                completed_at=at,
            )
        )
        db.commit()


def _set(name: str, version_id: str) -> str:
    with sync_session_factory()() as db:
        row = DetectorSet(
            id=f"dts_{name}",
            name=name,
            created_by="Test Operator",
            created_by_origin="operator",
        )
        db.add(row)
        db.flush()
        db.add(
            DetectorSetRole(
                id=f"dsr_{name}",
                set_id=row.id,
                role="train",
                version_id=version_id,
                split="train",
                input_column="text",
                label_column="label",
                label_mapping={"1": "positive", "0": "negative"},
            )
        )
        db.commit()
        return row.id


@pytest.fixture
def populated(clean_db: None) -> Iterator[dict[str, str]]:
    with sync_session_factory()() as db:
        reported = db_factories.version(db)
        plain = db_factories.version(db)
        db.commit()
        ids = {"reported": str(reported.id), "plain": str(plain.id)}
    now = datetime.now(UTC)
    _report(ids["reported"], ["src_old"], now - timedelta(days=1))
    _report(ids["reported"], ["src_a", "src_b"], now)
    ids["with_report"] = _set("with-report", ids["reported"])
    ids["without"] = _set("without-report", ids["plain"])
    # Review R4: a report whose benchmarks is not a list (a JSON null) neither fails the upgrade
    # nor names anything
    with sync_session_factory()() as db:
        odd = db_factories.version(db)
        db.commit()
        ids["odd"] = str(odd.id)
    _report(ids["odd"], None, now)
    ids["odd_set"] = _set("odd-report", ids["odd"])
    command.downgrade(_config(), "0041")
    yield ids
    command.upgrade(_config(), "head")


def _benchmarks(set_id: str) -> list[str]:
    with get_sync_engine().connect() as conn:
        value = conn.execute(
            text("SELECT benchmark_source_ids FROM dw_detector_sets WHERE id = :i"), {"i": set_id}
        ).scalar_one()
    return sorted(value)


def test_the_data_step_takes_the_newest_reports_benchmarks(populated: dict[str, str]) -> None:
    command.upgrade(_config(), "head")
    assert _benchmarks(populated["with_report"]) == ["src_a", "src_b"]
    assert _benchmarks(populated["without"]) == []
    assert _benchmarks(populated["odd_set"]) == []
    with sync_session_factory()() as db:
        version = db.get(Version, populated["reported"])
        assert version is not None and version.visibility_floor is None


def test_the_database_refuses_shapes_and_the_downgrade_refuses_while_used(
    populated: dict[str, str],
) -> None:
    command.upgrade(_config(), "head")
    with get_sync_engine().connect() as conn:
        conn.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        conn.commit()
    try:
        with sync_session_factory()() as db:
            with pytest.raises(IntegrityError):
                db.execute(
                    text("UPDATE dw_versions SET visibility_floor = 'public_only' WHERE id = :i"),
                    {"i": populated["plain"]},
                )
                db.commit()
            db.rollback()
            with pytest.raises(IntegrityError):
                db.execute(
                    text(
                        "UPDATE dw_detector_sets SET benchmark_source_ids = '{}'::jsonb WHERE id = :i"
                    ),
                    {"i": populated["without"]},
                )
                db.commit()
            db.rollback()
        with pytest.raises(RuntimeError, match="named benchmarks|set\\(s\\) naming"):
            command.downgrade(_config(), "0041")
        with get_sync_engine().connect() as conn:
            conn.execute(text("UPDATE dw_detector_sets SET benchmark_source_ids = '[]'::jsonb"))
            conn.execute(
                text("UPDATE dw_versions SET visibility_floor = 'private_only' WHERE id = :i"),
                {"i": populated["plain"]},
            )
            conn.commit()
        with pytest.raises(RuntimeError, match="visibility floor"):
            command.downgrade(_config(), "0041")
    finally:
        with get_sync_engine().connect() as conn:
            conn.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
            conn.commit()
