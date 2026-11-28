"""Migration 0046 (ENH-006 D116 G7) on a populated database: ``extra_eval`` may repeat and no other role
may; the downgrade removes only the rows the older constraint cannot hold."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.models.detector_set import DetectorSet, DetectorSetRole
from tests.support import db_factories


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


@pytest.fixture
def at_head(clean_db: None) -> Iterator[None]:
    yield
    command.upgrade(_config(), "head")


def _role(set_id: str, version_id: str, role_id: str, role: str) -> DetectorSetRole:
    return DetectorSetRole(
        id=role_id,
        set_id=set_id,
        role=role,
        position=0,
        version_id=version_id,
        split="train",
        input_column="text",
        label_column="label",
        label_mapping={"x": "positive"},
        label_source_columns=[],
        negatives_basis={"kind": "assumed_negative"} if role == "calibration_negatives" else None,
        display_name="",
    )


def _made() -> tuple[str, str]:
    with sync_session_factory()() as db:
        version = db_factories.version(db)
        row = DetectorSet(
            id="dts_m46", name="m46", created_by="Test Operator", created_by_origin="operator"
        )
        db.add(row)
        db.commit()
        return row.id, str(version.id)


def test_extra_eval_repeats_and_nothing_else_newly_does(at_head: None) -> None:
    set_id, version_id = _made()
    with sync_session_factory()() as db:
        db.add_all(
            [
                _role(set_id, version_id, "dsr_a", "extra_eval"),
                _role(set_id, version_id, "dsr_b", "extra_eval"),
                _role(set_id, version_id, "dsr_c", "ood_eval"),
                _role(set_id, version_id, "dsr_d", "ood_eval"),
                _role(set_id, version_id, "dsr_e", "id_test"),
            ]
        )
        db.commit()
        db.add(_role(set_id, version_id, "dsr_f", "id_test"))
        with pytest.raises(IntegrityError, match="uq_dw_detector_set_roles_single"):
            db.commit()
        db.rollback()
        db.add(_role(set_id, version_id, "dsr_g", "not_a_role"))
        with pytest.raises(IntegrityError, match="role_valid"):
            db.commit()


def test_the_downgrade_removes_only_extra_eval_and_restores_the_old_rules(at_head: None) -> None:
    set_id, version_id = _made()
    with sync_session_factory()() as db:
        db.add_all(
            [
                _role(set_id, version_id, "dsr_a", "extra_eval"),
                _role(set_id, version_id, "dsr_b", "extra_eval"),
                _role(set_id, version_id, "dsr_c", "ood_eval"),
            ]
        )
        db.commit()
    command.downgrade(_config(), "0045")
    with get_sync_engine().connect() as conn:
        left = (
            conn.execute(text("SELECT id FROM dw_detector_set_roles ORDER BY id")).scalars().all()
        )
    assert left == ["dsr_c"]
    with pytest.raises(IntegrityError, match="role_valid"), get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO dw_detector_set_roles (id, set_id, role, position, version_id, split, "
                "input_column, label_column, label_mapping, label_source_columns, display_name) "
                "VALUES ('dsr_x', :s, 'extra_eval', 0, :v, 'train', 'text', 'label', "
                "'{\"x\": \"positive\"}', '[]', '')"
            ),
            {"s": set_id, "v": version_id},
        )
    command.upgrade(_config(), "head")
    with get_sync_engine().connect() as conn:
        definition = conn.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": "uq_dw_detector_set_roles_single"},
        ).scalar_one()
    assert "extra_eval" in definition
