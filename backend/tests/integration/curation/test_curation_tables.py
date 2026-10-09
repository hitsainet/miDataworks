"""Feature 004's tables on real PostgreSQL 15 (FTASKS 2.3).

Each check here is a database fact, not a service rule: a completed report cannot change, a level
written with agent origin cannot exist, an empty reason or an out-of-range margin cannot be stored.
The service refuses first; these are the second wall (P-09).
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.database import sync_session_factory
from src.models import ShortcutLevel, VersionReport
from tests.support import db_factories as f

H = hashlib.sha256(b"x").hexdigest()


def _report(version_id: str, state: str = "running") -> VersionReport:
    return VersionReport(
        id=str(uuid.uuid4()),
        version_id=version_id,
        kind="shortcut_audit",
        operator_name="shortcut_audit",
        operator_version="1.0.0",
        manifest_hash=H,
        params_hash=H,
        params={"label_column": "label"},
        inputs=[{"version_id": version_id}],
        inputs_digest=H,
        seed=7,
        state=state,
        result={"columns": []} if state == "completed" else None,
        completed_at=None,
        started_by="Test Operator",
        started_by_origin="operator",
    )


def test_completed_report_cannot_be_updated(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version = f.version(db)
        row = _report(version.id)
        db.add(row)
        db.commit()
        db.execute(
            text(
                "UPDATE dw_version_reports SET state='completed', result='{}'::jsonb, "
                "completed_at=now() WHERE id=:id"
            ),
            {"id": row.id},
        )
        db.commit()
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(
                text("UPDATE dw_version_reports SET result='{\"x\": 1}'::jsonb WHERE id=:id"),
                {"id": row.id},
            )
            db.commit()
        db.rollback()
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text("DELETE FROM dw_version_reports WHERE id=:id"), {"id": row.id})
            db.commit()


def test_running_report_may_complete_and_two_completed_duplicates_conflict(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version = f.version(db)
        complete = text(
            "UPDATE dw_version_reports SET state='completed', result='{}'::jsonb, "
            "completed_at=now() WHERE id=:id"
        )
        rows = [_report(version.id), _report(version.id)]
        for row in rows:
            db.add(row)
        db.flush()
        db.execute(complete, {"id": rows[0].id})  # running -> completed is allowed
        with pytest.raises(IntegrityError, match="uq_dw_version_reports_completed"):
            db.execute(complete, {"id": rows[1].id})


def test_completed_without_result_is_refused(clean_db: None) -> None:
    with sync_session_factory()() as db:
        version = f.version(db)
        row = _report(version.id)
        row.state = "completed"
        db.add(row)
        with pytest.raises(IntegrityError, match="completed_has_result"):
            db.commit()


def _level(**overrides: object) -> ShortcutLevel:
    fields: dict[str, object] = {
        "scope": "global",
        "dataset_id": None,
        "action": "set",
        "margin_pp": Decimal("10"),
        "reason": "P-19 default",
        "set_by": "Test Operator",
        "origin": "operator",
    }
    fields.update(overrides)
    return ShortcutLevel(**fields)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"origin": "agent"}, "origin_operator_only"),
        ({"reason": "   "}, "reason_present"),
        ({"margin_pp": Decimal("100")}, "margin_range"),
        ({"margin_pp": Decimal("-1")}, "margin_range"),
        ({"action": "clear", "margin_pp": None}, "clear_only_for_dataset"),
        ({"scope": "dataset"}, "scope_dataset_coherent"),
        ({"margin_pp": None}, "margin_range"),
    ],
)
def test_level_checks(clean_db: None, overrides: dict[str, object], constraint: str) -> None:
    with sync_session_factory()() as db:
        db.add(_level(**overrides))
        with pytest.raises(IntegrityError, match=constraint):
            db.commit()


def test_valid_levels_are_stored(clean_db: None) -> None:
    with sync_session_factory()() as db:
        ds = f.dataset(db)
        db.add(_level())
        db.add(_level(scope="dataset", dataset_id=ds.id, margin_pp=Decimal("25.5")))
        db.add(_level(scope="dataset", dataset_id=ds.id, action="clear", margin_pp=None))
        db.commit()
        assert db.execute(text("SELECT count(*) FROM dw_shortcut_levels")).scalar_one() == 3
