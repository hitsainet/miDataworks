"""``DetectorSetError`` and its codes (FTDD 009 section 5.7).

Every domain refusal raises this one family, which subclasses Foundation's :class:`AppError`, so the
one handler renders it as the ADR-013 envelope. Every message says what to do next (R-03.62).
"""

from __future__ import annotations

from typing import Any

from ...core.errors import AppError

#: code -> HTTP status. The codes are FTDD 009 section 5.7's, plus three this implementation needed
#: (recorded in the controls review): ``name_taken``, ``set_not_found``, ``builds_pending``.
CODES: dict[str, int] = {
    "version_incomplete": 409,
    "label_mapping_invalid": 422,
    "roles_incomplete": 422,
    "send_refused": 409,
    "send_in_progress": 409,
    "approval_mismatch": 409,
    "mistudio_unreachable": 502,
    "mistudio_not_json": 502,
    "mistudio_dataset_exists": 409,
    "mistudio_download_failed": 502,
    "mistudio_download_timeout": 504,
    "mistudio_not_configured": 409,
    "registration_refused": 422,
    "counts_mismatch": 409,
    "reproduction_failed": 409,
    "probe_not_separate": 409,
    "model_not_resident": 409,
    "route_not_served": 409,
    "read_point_mismatch": 409,
    "name_taken": 409,
    "set_not_found": 404,
    "send_not_found": 404,
    "report_not_found": 404,
    "send_terminal": 409,
    "already_marked": 409,
    "rows_disjoint": 422,
    "builds_pending": 409,
    "set_referenced": 409,
    "namespace_required": 422,
    "monitored_ref_missing": 422,
    "role_invalid": 422,
    # Reproduction links (FR-009.77 option (b), 2026-10-07).
    "evaluation_not_found": 404,
    "link_rows_differ": 409,
    "link_exists": 409,
    "link_not_found": 404,
    "link_in_use": 409,
}


class DetectorSetError(AppError):
    """A 009 refusal with its code, HTTP status and details."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        if code not in CODES:
            raise ValueError(f"unknown DetectorSetError code {code!r}")
        super().__init__(message, code=code, status_code=CODES[code], details=details)
