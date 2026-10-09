"""``GET /datasets/meta`` is built from the enums, scheme registry and defaults table (task 3.1).

Compared against the sources themselves, not against a copy in this file, so a value added to an
enum appears in the endpoint with no second edit — and a hand-kept list in the service would fail.
"""

from __future__ import annotations

import httpx
import pytest

from src.models import enums
from src.services.assembly import DEFAULT_CONTENT_COLUMNS
from src.services.row_keys import ROWKEY_SCHEMES


async def _meta() -> dict[str, object]:
    from src.main import fastapi_app

    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        response = await http.get("/api/v1/datasets/meta")
    assert response.status_code == 200, response.text
    data: dict[str, object] = response.json()
    return data


async def test_meta_equals_the_enum_sources() -> None:
    meta = await _meta()
    assert meta["target_types"] == [m.value for m in enums.TargetType]
    assert meta["event_kinds"] == [m.value for m in enums.EventKind]
    assert meta["version_states"] == [m.value for m in enums.VersionState]
    assert meta["input_kinds"] == [m.value for m in enums.InputKind]
    assert meta["column_roles"] == [m.value for m in enums.ColumnRole]
    assert meta["binding_kinds"] == [m.value for m in enums.BindingKind]
    assert meta["guided_steps"] == list(enums.GUIDED_STEPS)
    assert meta["rowkey_schemes"] == sorted(ROWKEY_SCHEMES)
    assert meta["default_content_columns"] == {
        k: list(v) for k, v in DEFAULT_CONTENT_COLUMNS.items()
    }


async def test_a_new_enum_value_reaches_the_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ROWKEY_SCHEMES, "dw.rowkey/probe", ROWKEY_SCHEMES["dw.rowkey/v1"])
    assert "dw.rowkey/probe" in (await _meta())["rowkey_schemes"]  # type: ignore[operator]


def test_every_check_constraint_uses_the_enum_values() -> None:
    """The CHECK text comes from check_in(), so the database and the endpoint share one list."""
    from src.models import Dataset, RowEvent, StepExecution, Version

    def checks(model: type) -> str:
        return " ".join(
            str(c.sqltext) for c in model.__table__.constraints if hasattr(c, "sqltext")
        )

    assert enums.check_in("target_type", enums.TargetType) in checks(Dataset)
    assert enums.check_in("state", enums.VersionState) in checks(Version)
    assert enums.check_in("kind", enums.EventKind) in checks(RowEvent)
    assert enums.check_in("state", enums.StepState) in checks(StepExecution)
