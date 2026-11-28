"""``RuleSetError``: every 016 refusal in the ADR-013 envelope, with ``details.next`` (FR-016.36)."""

from __future__ import annotations

from typing import Any

from ...core.errors import AppError
from ...rules.next_step import next_for


class RuleSetError(AppError):
    """A refusal naming the item and, where an agent can act, the next tool to call."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        next_tool: str | None = None,
        next_args: dict[str, Any] | None = None,
        why: str = "",
    ) -> None:
        body = dict(details or {})
        if next_tool is not None:
            body["next"] = next_for(next_tool, next_args, why)
        super().__init__(message, code=code, status_code=status, details=body)


def problem(error: RuleSetError) -> dict[str, Any]:
    """A refusal as a list item (validate collects them instead of stopping)."""
    return {"code": error.code, "message": error.message, "details": error.details}
