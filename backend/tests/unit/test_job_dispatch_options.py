"""A job kind's send options reach the Celery send at dispatch (016 FTID I11: the derivation's
soft time limit is the runtime setting in force when the job is sent)."""

from __future__ import annotations

from typing import Any

import pytest

from src.core.config import get_settings
from src.core.database import sync_session_factory
from src.core.ids import new_id
from src.models import Job
from src.services.job_service import dispatch_queued


def test_the_derivation_is_sent_with_its_soft_time_limit(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rule_derivation_time_limit_s", 777)
    sent: list[tuple[str, dict[str, Any]]] = []
    with sync_session_factory()() as db:
        db.add(
            Job(
                id=new_id("job"),
                kind="rule_derivation",
                status="queued",
                progress=0.0,
                params={"derivation_id": "rdv_x"},
                started_by="Test Operator",
                started_by_origin="operator",
            )
        )
        db.commit()
        dispatch_queued(db, send=lambda name, **kw: sent.append((name, kw)))
    assert [(n, kw["soft_time_limit"]) for n, kw in sent] == [
        ("midataworks.rules.derive_rule_set", 777)
    ]
