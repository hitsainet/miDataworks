"""REST bodies of the code-rule routes (feature 018; FTDD §6.1).

The document and its cases are taken as JSON and validated by the contract's own models
(``runner_contract.document``) inside the services, so each refusal is a named 018 code with its
JSON Pointer (``code_rule_invalid``), never FastAPI's generic 422.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..rules.rule_set import NAME_PATTERN
from .behaviour import BEHAVIOUR_KEY_PATTERN

FUNCTION_NAME = r"^[a-z_][a-z0-9_]{0,63}$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeRuleSaveIn(_In):
    """A save: the name, its one behaviour key, the document (hashed) and its cases (not)."""

    name: str = Field(pattern=NAME_PATTERN)
    behaviour_key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    document: dict[str, Any]
    cases: list[dict[str, Any]]


class CodeRuleTestIn(_In):
    """A dry run: a document inline (with any cases), or a stored ``name`` and ``version``."""

    document: dict[str, Any] | None = None
    cases: list[dict[str, Any]] | None = None
    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    version: int | None = Field(default=None, ge=1)
    texts: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    functions: list[str] | None = Field(default=None, max_length=64)


class ApprovalActionIn(_In):
    """Revoke or approve again: the operator says why (kept in the hash's history)."""

    reason: str = Field(min_length=1, max_length=500)
