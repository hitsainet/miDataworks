"""Migration 0041 (017 slice 6, FTASKS 9.1) on a POPULATED database.

A label run, a review queue and a configured endpoint role survive 0041 down and up with the new
columns NULL (nothing is invented); after 0041 the database refuses a negative price, a stop reason
outside the three, and a cost basis that is not an object; the downgrade refuses while a priced
role or a costed run exists (dropping them would forget what was paid for).
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from alembic import command
from src.core.database import get_sync_engine, sync_session_factory
from src.models.endpoint_role import EndpointRole
from src.models.label_run import LabelRun
from tests.support import db_factories


def _config() -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["skip_logging"] = True
    return config


def _run(**over: object) -> str:
    with sync_session_factory()() as db:
        version = db_factories.version(db)
        db.commit()
        run = LabelRun(
            id=f"lr_{db_factories.uid()[:20]}",
            kind="classifier",
            state="completed",
            input_version_id=str(version.id),
            chunk_size=100,
            labeler_identity={"kind": "classifier"},
            labeler_identity_hash="b" * 64,
            labeler_fingerprint="c" * 64,
            started_by="Test Operator",
            started_by_origin="operator",
            **over,
        )
        db.add(run)
        db.commit()
        return run.id


def _role(**over: object) -> None:
    with sync_session_factory()() as db:
        db.add(EndpointRole(role="classifier", protocol="typesafe_systemone", **over))
        db.commit()


def _columns(table: str) -> set[str]:
    with get_sync_engine().connect() as conn:
        return set(
            conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
                {"t": table},
            ).scalars()
        )


@pytest.fixture
def populated(clean_db: None) -> Iterator[str]:
    run_id = _run()
    _role(base_url="https://api.example.test/v1/systemone")
    yield run_id
    command.upgrade(_config(), "head")


def test_0041_round_trips_a_populated_database(populated: str) -> None:
    command.downgrade(_config(), "0040")
    assert not {"target_column", "handoff_id", "cost_basis", "stop_reason"} & _columns(
        "dw_label_runs"
    )
    assert "price_per_call_usd" not in _columns("dw_endpoint_roles")
    assert "handoff_id" not in _columns("dw_review_queues")
    command.upgrade(_config(), "head")
    with sync_session_factory()() as db:
        run = db.get(LabelRun, populated)
        assert run is not None
        assert (run.target_column, run.handoff_id, run.cost_basis, run.stop_reason) == (
            None,
            None,
            None,
            None,
        )
        role = db.get(EndpointRole, "classifier")
        assert role is not None and role.price_per_call_usd is None


def test_the_database_refuses_a_negative_price_and_keeps_six_decimals(populated: str) -> None:
    with sync_session_factory()() as db:
        role = db.get(EndpointRole, "classifier")
        assert role is not None
        role.price_per_call_usd = Decimal("-0.000001")
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        role = db.get(EndpointRole, "classifier")
        assert role is not None
        role.price_per_call_usd = Decimal("0.000800")
        db.commit()
        db.refresh(role)
        assert role.price_per_call_usd == Decimal("0.000800")


@pytest.mark.parametrize(
    "bad",
    [
        {"stop_reason": "worker_lost"},
        {"cost_basis": [1, 2]},
    ],
)
def test_the_database_refuses_a_stop_reason_or_cost_basis_outside_its_shape(
    populated: str, bad: dict[str, object]
) -> None:
    with pytest.raises(IntegrityError):
        _run(**bad)
    for good in ({"stop_reason": "cap_reached"}, {"cost_basis": {"estimated_usd": 0.1}}):
        _run(**good)


def test_the_downgrade_refuses_while_a_price_or_a_cost_basis_exists(populated: str) -> None:
    with sync_session_factory()() as db:
        role = db.get(EndpointRole, "classifier")
        assert role is not None
        role.price_per_call_usd = Decimal("0.0008")
        db.commit()
    with pytest.raises(RuntimeError, match="priced endpoint role"):
        command.downgrade(_config(), "0040")
    assert "price_per_call_usd" in _columns("dw_endpoint_roles"), "nothing was dropped"
    with sync_session_factory()() as db:
        db.execute(text("UPDATE dw_endpoint_roles SET price_per_call_usd = NULL"))
        db.commit()
    _run(cost_basis={"estimated_usd": 0.2})
    with pytest.raises(RuntimeError, match="cost basis"):
        command.downgrade(_config(), "0040")
