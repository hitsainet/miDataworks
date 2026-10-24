"""The ``next`` field (FR-016.36; FTDD 016 section 6.3): what an agent does after a result.

Shape: ``{"tool": "<registered MCP tool>", "args": {...}, "why": "<one line>"}``. On a success it is a
top-level field of the body; on a refusal it is ``error.details.next``. The backend never imports the
MCP package, so the names cannot be checked here: ``tests/unit/rules/test_next_names_registered_tools.py``
drives every refusal and success of the rule routes and asserts each ``tool`` is in the LIVE registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def next_for(tool: str, args: Mapping[str, Any] | None = None, why: str = "") -> dict[str, Any]:
    """One ``next`` value. ``tool`` must name a registered MCP tool (the test above pins it)."""
    if not tool.startswith("dataworks_"):
        raise ValueError(f"next names an MCP tool (dataworks_*), not {tool!r}")
    return {"tool": tool, "args": dict(args or {}), "why": why}
