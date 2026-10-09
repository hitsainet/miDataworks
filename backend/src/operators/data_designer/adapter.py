"""Data Designer operators, backend side (FR-003.15; PADR ADR-010 amendment 2026-10-07).

The backend NEVER imports ``data_designer`` (``test_designer_runner_imports.py`` walks every backend
module for it). A Data Designer step goes to the designer worker by task name, like Data-Juicer:
this module builds the plain-JSON payload. The endpoint's URL and model travel in it; its key is
sealed into Redis by ``handoff.put`` and only its reference travels.

**Open item for feature 007 (spike 1.4, 2026-10-07):** Data Designer 0.9.4 sends ``extra_body``
STATICALLY — a ``{{ _dw_row_key }}`` template arrives literally — so a Data Designer request cannot
carry its row key, and the relay's records cannot be joined to rows by key. The finaliser joins
output to rows by the carried key columns instead, and attributes failures to rows in request
order (one request per row, ``max_parallel_requests=1``). FR-007.34's per-row steering header
therefore needs either per-row correlation upstream or 007's own native generator on the relay.
"""

from __future__ import annotations

from typing import Any

from ...services.operator_port import StepSpec
from ..endpoint_port import ResolvedEndpoint


def raw_dir(output_dir: str) -> str:
    """Where the designer worker writes its output: beside the step's output dir."""
    return f"{output_dir}.dd"


def seed_columns(column_roles: dict[str, str]) -> list[str]:
    return sorted(c for c, role in column_roles.items() if role == "content")


def designer_payload(
    entry: Any,
    params: dict[str, Any],
    column_roles: dict[str, str],
    endpoint: ResolvedEndpoint | None,
    key_ref: str | None,
    *,
    lease_id: str | None = None,
    body_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The plain-JSON part every designer task shares (no ``src`` types, never the key)."""
    default = entry.manifest.params_schema["properties"]["output_column"].get("default")
    return {
        "operator": entry.ref,
        "column_class": entry.extra["column_class"],
        "column_fields": list(entry.extra.get("column_fields", [])),
        "params": dict(params),
        "params_schema": entry.manifest.params_schema,
        "output_column": str(params.get("output_column") or default),
        "seed_columns": seed_columns(column_roles),
        "endpoint": (
            None
            if endpoint is None
            else {
                "base_url": endpoint.base_url,
                "model": endpoint.model,
                "is_millm": endpoint.is_millm,
            }
        ),
        "key_ref": key_ref,
        "lease_id": lease_id,
        "body_overrides": body_overrides,
    }


def step_payload(
    entry: Any, spec: StepSpec, endpoint: ResolvedEndpoint | None, key_ref: str | None
) -> dict[str, Any]:
    return {
        **designer_payload(entry, spec.params, spec.column_roles, endpoint, key_ref),
        "input_dir": spec.input_dir,
        "output_dir": raw_dir(spec.output_dir),
        "job_id": spec.job_id,
        "step_execution_id": spec.step_execution_id,
    }
