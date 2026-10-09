"""Every operator error code renders the one envelope with its status (FTASKS 10.3)."""

from __future__ import annotations

import json

import pytest

from src.core.errors import _app_error
from src.operators.errors import STATUS_FOR_CODE, OperatorError
from src.services.operator_port import OperatorRefusal


@pytest.mark.parametrize(("code", "status"), sorted(STATUS_FOR_CODE.items()))
async def test_each_code_maps_to_its_status_and_envelope(code: str, status: int) -> None:
    error = OperatorError(code, f"message for {code}", {"k": 1})
    response = await _app_error(None, error)  # type: ignore[arg-type]
    assert response.status_code == status
    assert json.loads(response.body) == {
        "error": {"code": code, "message": f"message for {code}", "details": {"k": 1}}
    }
    assert isinstance(error, OperatorRefusal) and error.code == code


def test_params_invalid_is_422_and_unknown_codes_are_500() -> None:
    assert OperatorError("params_invalid", "x").status_code == 422
    assert OperatorError("something_new", "x").status_code == 500
