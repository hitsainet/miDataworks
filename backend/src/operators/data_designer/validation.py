"""Data Designer's OWN template rules, asked of the designer worker (PII run G4, 2026-10-20).

The backend never imports ``data_designer`` (``test_designer_runner_imports.py``), so validating a
``dd_*`` step's templates is a task on the ``designer`` queue (``midataworks.designer.validate`` in
``runner.py``), awaited here with ``DESIGNER_VALIDATE_TIMEOUT_S``. Before this, the operator and
recipe validators checked the JSON schema only and said "valid" for templates Data Designer then
refused with one generic sentence. When the worker does not answer, the answer is
``503 designer_unavailable``: a template whose rules were not checked is never called valid.
"""

from __future__ import annotations

from typing import Any

from ...core.config import get_settings
from ..errors import OperatorError
from .adapter import visible_columns

VALIDATE_TASK = "midataworks.designer.validate"


def is_designer(entry: Any) -> bool:
    manifest = getattr(entry, "manifest", None)
    return manifest is not None and manifest.resources.queue == "designer"


def send_validate(payload: dict[str, Any]) -> Any:
    """Send the task; returns the Celery result handle (tests replace this)."""
    from ...core.celery_app import celery_app

    return celery_app.send_task(VALIDATE_TASK, args=[payload])


def payload_for(entry: Any, params: dict[str, Any], columns: list[str] | None) -> dict[str, Any]:
    default = entry.manifest.params_schema["properties"]["output_column"].get("default")
    return {
        "operator": entry.ref,
        "column_class": entry.extra["column_class"],
        "column_fields": list(entry.extra.get("column_fields", [])),
        "params": dict(params),
        "output_column": str(params.get("output_column") or default),
        "columns": (
            None
            if columns is None
            else visible_columns({c: "content" for c in columns if not c.startswith("_dw_")})
        ),
    }


def check_templates(
    entry: Any, params: dict[str, Any], columns: list[str] | None = None
) -> dict[str, Any]:
    """The worker's answer ``{valid, errors, references, limits}``; raises when it is silent."""
    from celery.exceptions import TimeoutError as CeleryTimeout

    timeout = get_settings().designer_validate_timeout_s
    try:
        answer: dict[str, Any] = send_validate(payload_for(entry, params, columns)).get(
            timeout=timeout
        )
    except CeleryTimeout:
        raise OperatorError(
            "designer_unavailable",
            f"The Data Designer worker did not answer in {timeout:.0f} s, so this template was "
            "NOT checked against Data Designer's rules (the parameter schema was). Check "
            "dataworks_health and validate again.",
            {"operator": entry.ref},
        ) from None
    except Exception as exc:  # noqa: BLE001 - a worker crash is reported, never a silent pass
        raise OperatorError(
            "designer_unavailable",
            f"The Data Designer worker could not check this template ({type(exc).__name__}: "
            f"{exc}). It was NOT checked against Data Designer's rules.",
            {"operator": entry.ref},
        ) from None
    return answer


def require_valid_templates(
    entry: Any, params: dict[str, Any], columns: list[str] | None = None
) -> dict[str, Any]:
    """Raise ``template_invalid`` naming every failed rule; returns the answer when valid."""
    answer = check_templates(entry, params, columns)
    if not answer.get("valid"):
        errors = list(answer.get("errors") or [])
        raise OperatorError(
            "template_invalid",
            "Data Designer would refuse this step: "
            + "; ".join(f"{e.get('field') or 'config'}: {e.get('message')}" for e in errors),
            {"errors": errors, "limits": answer.get("limits")},
        )
    return answer
