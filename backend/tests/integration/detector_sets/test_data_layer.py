"""009's tables on a real PostgreSQL: the single-role index, the frozen send columns, the
insert-only tables, and the migration's down refusal (FTASKS 2.3 - 2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.core.ids import new_id
from src.models import DetectorResults, DetectorSend, DetectorSet, DetectorSetRole, RewardMark
from tests.support import db_factories
from tests.support.calibration_fixtures import make_run
from tests.support.calibration_fixtures import make_version as cal_version


def _set(db: object) -> DetectorSet:
    row = DetectorSet(
        id=new_id("dts"), name=f"s-{new_id('x')[-8:]}", created_by="T", created_by_origin="operator"
    )
    db.add(row)  # type: ignore[attr-defined]
    db.flush()  # type: ignore[attr-defined]
    return row


def _role(set_id: str, role: str, version_id: str, position: int = 0) -> DetectorSetRole:
    return DetectorSetRole(
        id=new_id("dsr"),
        set_id=set_id,
        role=role,
        position=position,
        version_id=version_id,
        split="train",
        input_column="text",
        label_column="label",
        label_mapping={"a": "positive"},
        negatives_basis={"kind": "assumed_negative"} if role == "calibration_negatives" else None,
    )


def test_a_second_train_role_fails_at_the_database_and_ood_may_repeat(clean_db: None) -> None:
    with sync_session_factory()() as db:
        v = db_factories.version(db)
        s = _set(db)
        db.add_all(
            [
                _role(s.id, "train", v.id),
                _role(s.id, "ood_eval", v.id),
                _role(s.id, "ood_eval", v.id, 1),
            ]
        )
        db.commit()
        db.add(_role(s.id, "train", v.id))
        with pytest.raises(IntegrityError, match="uq_dw_detector_set_roles_single"):
            db.commit()
        db.rollback()
        db.add(_role(s.id, "calibration_negatives", v.id))
        db.commit()
        db.add(_role(s.id, "calibration_negatives", v.id))
        with pytest.raises(IntegrityError):
            db.commit()


def test_calibration_negatives_need_a_basis(clean_db: None) -> None:
    with sync_session_factory()() as db:
        v = db_factories.version(db)
        s = _set(db)
        bad = _role(s.id, "calibration_negatives", v.id)
        bad.negatives_basis = None
        db.add(bad)
        with pytest.raises(IntegrityError, match="calibration_has_basis"):
            db.commit()


def _send(db: object, set_id: str) -> DetectorSend:
    job = db_factories.job(db, kind="selftest")  # type: ignore[arg-type]
    row = DetectorSend(
        id=new_id("dsn"),
        set_id=set_id,
        job_id=job.id,
        job_ids=[job.id],
        snapshot={"roles": []},
        snapshot_sha256="0" * 64,
        checks=[],
        plan={"roles": []},
        mistudio_base_url="http://m",
        approval_digest="1" * 64,
        state="queued",
        started_by="T",
        started_by_origin="operator",
    )
    db.add(row)  # type: ignore[attr-defined]
    db.flush()  # type: ignore[attr-defined]
    return row


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("snapshot", "'{\"roles\": [1]}'::jsonb"),
        ("snapshot_sha256", "repeat('2', 64)"),
        ("checks", "'[{}]'::jsonb"),
        ("plan", "'{}'::jsonb"),
        ("approval_digest", "repeat('3', 64)"),
    ],
)
def test_a_sends_snapshot_checks_plan_and_digest_are_written_once(
    clean_db: None, column: str, value: str
) -> None:
    with sync_session_factory()() as db:
        send = _send(db, _set(db).id)
        db.commit()
        send_id = send.id
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_detector_sends SET state = 'running' WHERE id = :i"), {"i": send_id}
        )
    with pytest.raises(DBAPIError, match="written once"):
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(f"UPDATE dw_detector_sends SET {column} = {value} WHERE id = :i"),
                {"i": send_id},
            )


def test_results_and_reward_marks_are_insert_only(clean_db: None) -> None:
    with sync_session_factory()() as db:
        s = _set(db)
        send = _send(db, s.id)
        db.add(
            DetectorResults(
                id=new_id("dres"),
                set_id=s.id,
                send_id=send.id,
                mistudio_base_url="http://m",
                read_by="T",
                read_by_origin="operator",
                runs=[],
                figures=[],
                report_sha256={},
                gone={},
            )
        )
        db.add(
            RewardMark(
                id=new_id("rwm"),
                mistudio_base_url="http://m",
                mistudio_probe_id="pm_1",
                source="operator",
                reason="used as a reward",
                marked_by="T",
                marked_by_origin="operator",
            )
        )
        db.commit()
    for table in ("dw_detector_results", "dw_reward_marks"):
        with pytest.raises(DBAPIError, match="append-only"):
            with get_sync_engine().begin() as conn:
                conn.execute(text(f"DELETE FROM {table}"))


def _config() -> Config:
    root = Path(__file__).resolve().parents[3]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def test_down_refuses_while_probe_verdict_runs_exist_and_round_trips_otherwise(
    clean_db: None, data_dir: Path
) -> None:
    config = _config()
    try:
        command.downgrade(config, "0015")
        command.upgrade(config, "head")
        vid = cal_version(data_dir, [{"text": "a"}, {"text": "b"}])
        run = make_run(vid, {})
        with get_sync_engine().begin() as conn:
            conn.execute(
                text("UPDATE dw_label_runs SET kind = 'probe_verdict' WHERE id = :i"), {"i": run.id}
            )
        with pytest.raises(RuntimeError, match="probe_verdict or feature_tag"):
            command.downgrade(config, "0015")
    finally:
        command.upgrade(config, "head")
