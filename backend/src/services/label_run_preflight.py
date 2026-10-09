"""The label-run preflight registry (FR-005.54; FTDD 005 section 6.5b; Stage 3, requested by 007).

Other features register checks that run when a label run is PLANNED, after the plan is built and
before the approval gate, so a refused run creates no approval and no job. A check receives the
plan context and either returns or raises :class:`PreflightRefused` with a stable code; the API
answers ``422`` with that code.

Registration is by appending in the feature's own module, imported from
``label_run_preflight_registrations.py``. Feature 007 registers ``JUDGE_IS_GENERATOR`` there once
it lands; a live-registry reachability test asserts each expected check is present.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class PreflightRefused(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class PreflightContext:
    """What a check sees: the input version, role, resolved endpoint and model, row filter."""

    input_version_id: str
    role: str
    protocol: str
    base_url: str
    model_id: str
    model_revision: str | None
    labeler_identity: dict[str, Any]
    labeler_identity_hash: str
    row_filter: dict[str, str] | None
    rows_to_score: int
    template_id: str | None
    rubric_id: str | None


PreflightCheck = Callable[[PreflightContext], None]

#: The live registry, in registration order.
PREFLIGHT_CHECKS: list[PreflightCheck] = []


def register(check: PreflightCheck) -> PreflightCheck:
    """Add a check once (a module imported twice must not double-register)."""
    if check not in PREFLIGHT_CHECKS:
        PREFLIGHT_CHECKS.append(check)
    return check


def run_checks(context: PreflightContext) -> None:
    """Run every registered check; the first refusal propagates."""
    from . import label_run_preflight_registrations  # noqa: F401 - registers by import

    for check in list(PREFLIGHT_CHECKS):
        check(context)
