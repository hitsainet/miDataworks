"""``CodeRuleError``: every 018 refusal in the ADR-013 envelope, with ``details.next`` (FR-018.36).

A refusal names the item it refers to (a JSON Pointer, a case id, a function, a setting) and, where an
agent can act, the next tool to call. It never carries a source or a text (FR-018.25): a load
failure names its exception type and line, never its message.
"""

from __future__ import annotations

from typing import Any

from ...core.errors import AppError
from ...rules.next_step import next_for


class CodeRuleError(AppError):
    """A refusal naming the item and the next tool to call."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        next_tool: str,
        next_args: dict[str, Any] | None = None,
        why: str = "",
    ) -> None:
        body = dict(details or {})
        body["next"] = next_for(next_tool, next_args, why)
        super().__init__(message, code=code, status_code=status, details=body)


def disabled() -> CodeRuleError:
    return CodeRuleError(
        409,
        "code_rules_disabled",
        "Code rules are switched off in this deployment (CODE_RULES_ENABLED). Only the operator "
        "turns them on, after the runner's post-deploy check.",
        {"setting": "CODE_RULES_ENABLED"},
        next_tool="dataworks_howto",
        next_args={"topic": "code_rules"},
        why="read how code rules are switched on and by whom",
    )


def runner_unavailable(detail: str) -> CodeRuleError:
    return CodeRuleError(
        503,
        "runner_unavailable",
        f"The code-rule runner cannot be reached ({detail}); nothing was run or stored. Try "
        "again later; builds read rule_worker_unavailable for these rows meanwhile.",
        {"runner": detail},
        next_tool="dataworks_health",
        why="see whether the runner is up",
    )


def not_found(name: str, version: int | None = None) -> CodeRuleError:
    label = name if version is None else f"{name}@{version}"
    return CodeRuleError(
        404,
        "code_rule_not_found",
        f"No stored code rule {label}.",
        {"name": name, "version": version},
        next_tool="dataworks_list_code_rules",
        why="find a stored code rule",
    )
