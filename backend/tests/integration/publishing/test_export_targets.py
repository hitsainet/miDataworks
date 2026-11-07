"""Every export target is implemented; no target answers "not available in this build".

Derived from ``ExportTarget`` (the authority), not from a hand-kept list: each member must pass the
request schema and the service's target gate and reach the version lookup. Until 2026-10-17 the
service refused any other value as "not available in this build", copy that claimed a missing
feature where there was only a bad value.
"""

from __future__ import annotations

import typing
import uuid

import pytest

from src.core.database import sync_session_factory
from src.core.errors import AppError
from src.models.publish import ExportTarget
from src.schemas.publishing import ExportIn
from src.services.exports.export_service import request_export


def test_the_request_schema_accepts_exactly_the_export_targets() -> None:
    accepted = set(typing.get_args(ExportIn.model_fields["target"].annotation))
    assert accepted == {t.value for t in ExportTarget}


@pytest.mark.parametrize("target", [t.value for t in ExportTarget])
def test_every_target_passes_the_gate_and_reaches_the_version(target: str) -> None:
    missing = str(uuid.uuid4())
    with sync_session_factory()() as session, pytest.raises(AppError) as caught:
        request_export(
            session, {"target": target, "version_id": missing}, started_by="t", origin="operator"
        )
    assert caught.value.code == "version_not_found", (target, caught.value.code)


def test_a_value_that_is_not_a_target_is_named_as_such() -> None:
    with sync_session_factory()() as session, pytest.raises(AppError) as caught:
        request_export(
            session, {"target": "csv", "version_id": "v"}, started_by="t", origin="operator"
        )
    assert caught.value.code == "export_target_unknown"
    assert caught.value.message == (
        "'csv' is not an export target; choose one of trl, miforge_set, reward_bundle."
    )
    assert "not available" not in caught.value.message
