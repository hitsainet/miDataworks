"""Report kind -> compute function: the one table the API, the worker and the sweeper share.

A compute function takes ``(session, inputs, params, seed)`` and returns
``report_service.Computed``; it raises ``AuditRefusal``-style refusals with stable codes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import CurationError


def compute_for(kind: str) -> Callable[..., Any]:
    if kind == "shortcut_audit":
        from .audit_service import compute_audit

        return compute_audit
    if kind == "leakage":
        from .leakage_service import compute_leakage

        return compute_leakage
    if kind == "profile":
        from .profile_service import compute_profile

        return compute_profile
    if kind == "contamination":
        from .contamination_service import compute_contamination

        return compute_contamination
    if kind == "clusters":
        from .cluster_service import compute_clusters

        return compute_clusters
    raise CurationError("report_not_found", f"No report kind {kind!r}.")


def guarded(compute: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a service refusal into a ``CurationError`` with the same code and details."""

    def run(*args: Any, **kwargs: Any) -> Any:
        from .audit_service import AuditRefusal

        try:
            return compute(*args, **kwargs)
        except AuditRefusal as exc:
            raise CurationError(exc.code, exc.message, exc.details) from None

    return run
