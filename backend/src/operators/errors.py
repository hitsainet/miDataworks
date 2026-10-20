"""Typed operator errors with stable codes (FTID 003 sections 11 and 12; FTDD 003 section 5.2).

``OperatorError`` is both an :class:`~src.core.errors.AppError` (so the API renders the one
envelope, ADR-013) and feature 002's :class:`~src.services.operator_port.OperatorRefusal` (so
002's recipe validation and build orchestrator catch registry refusals as they already do).
The HTTP status for each code lives in ONE table, :data:`STATUS_FOR_CODE`.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import AppError
from ..services.operator_port import OperatorRefusal

#: Every code this feature raises, with its HTTP status. A code missing here is a 500, by design:
#: ``test_api_operators.py`` raises each one through the live app.
STATUS_FOR_CODE: dict[str, int] = {
    "operator_not_found": 404,
    "operator_not_allowed": 409,
    "operator_invalid_manifest": 409,
    "params_invalid": 422,
    "version_unavailable": 409,
    "manifest_mismatch": 409,
    "conservation_violated": 409,
    "kind_effect_violated": 409,
    "event_invalid": 409,
    "no_threshold": 409,
    "agent_forbidden": 403,
    "entry_point_not_installed": 404,
    "worker_unavailable": 503,
    "endpoint_role_missing": 409,
    "endpoint_unconfigured": 409,
    "preview_not_found": 404,
    "preview_timeout": 504,
    "preview_failed": 409,
    "input_not_found": 404,
    "relay_override_conflict": 422,
    "statistics_missing": 409,
    "reason_required": 422,
}


class OperatorError(AppError, OperatorRefusal):
    """A refusal with a stable code; see :data:`STATUS_FOR_CODE`."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = STATUS_FOR_CODE.get(code, 500)


class StepFailed(OperatorError):  # noqa: N818 - matches the FTID name feature 002 reads
    """A step violated the contract in the worker; feature 002's callback reads ``code``."""


def not_found(name: str, version: str | None = None) -> OperatorError:
    ref = f"{name}@{version}" if version else name
    return OperatorError(
        "operator_not_found",
        f"No operator {ref} is installed. Open the Operators screen to see what is available.",
        {"operator": name, "version": version},
    )


def not_allowed(ref: str, why: str = "") -> OperatorError:
    return OperatorError(
        "operator_not_allowed",
        f"{ref} is installed but not allowed{': ' + why if why else ''}. "
        "Allow its entry point on the Operators screen, or choose another operator.",
        {"operator": ref},
    )
