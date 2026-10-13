"""Feature 004's refusals with stable codes (FTID 004 §12; FPRD §7.1).

``CurationError`` is an :class:`~src.core.errors.AppError`, so the Foundation handler renders the
one envelope (ADR-013). The HTTP status for each code lives in ONE table, :data:`STATUS_FOR_CODE`;
``test_curation_routes.py`` raises each code once through the live app.
"""

from __future__ import annotations

from typing import Any

from ...core.errors import AppError

STATUS_FOR_CODE: dict[str, int] = {
    "no_label_column": 422,
    "single_class_label": 422,
    "insufficient_rows": 422,
    "no_splits": 422,
    "group_column_missing": 422,
    "unknown_target_type": 422,
    "benchmark_revision_unavailable": 422,
    "benchmark_not_found": 404,
    "role_unconfigured": 422,
    "embeddings_unavailable": 422,
    "override_reason_required": 422,
    "margin_out_of_range": 422,
    "agent_forbidden": 403,
    "version_not_found": 404,
    "version_deleted": 409,
    "dataset_not_found": 404,
    "profile_not_run": 404,
    "audit_not_run": 404,
    "leakage_not_run": 404,
    "contamination_not_run": 404,
    "clusters_not_run": 404,
    "report_not_found": 404,
    "cell_not_found": 404,
    "basis_unreproducible": 409,
    "empty_cell": 422,
    "invalid_balance_column": 422,
    "split_fractions_invalid": 422,
    "generated_in_held_out": 409,
}


class CurationError(AppError):
    """A refusal with a stable code; the status comes from :data:`STATUS_FOR_CODE`."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            message, code=code, status_code=STATUS_FOR_CODE.get(code, 500), details=details
        )
