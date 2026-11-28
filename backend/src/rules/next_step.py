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


#: The contract's ``result_fields.next_steps.maxItems``.
MAX_NEXT_STEPS = 5


def as_next_step(value: Any) -> dict[str, Any] | None:
    """One legacy ``next`` item in the contract's ``next_step`` shape (``{tool, args, why}`` and
    nothing else), or ``None`` when it names no tool. A legacy item may carry more (the behaviour
    journey's ``step``); the contract's element does not, so only the three fields are kept."""
    if not isinstance(value, Mapping) or not isinstance(value.get("tool"), str):
        return None
    args = value.get("args")
    why = str(value.get("why") or "").strip() or f"call {value['tool']}"
    return {
        "tool": value["tool"],
        "args": dict(args) if isinstance(args, Mapping) else {},
        "why": why[:300],
    }


def add_next_steps(mapping: dict[str, Any]) -> dict[str, Any]:
    """Set ``next_steps``, the suite contract's list (022 FR-022.8; mistudio.result-language/v1),
    beside a legacy ``next``: one item becomes a one-item list, a list keeps its order, ``None`` is
    ``[]``; each item in the contract's ``next_step`` shape (:func:`as_next_step`). ``next`` itself
    is never touched. Returns ``mapping`` (changed in place)."""
    if "next" in mapping and "next_steps" not in mapping:
        value = mapping["next"]
        items = [] if value is None else (value if isinstance(value, list) else [value])
        steps = [s for s in (as_next_step(i) for i in items) if s is not None]
        mapping["next_steps"] = steps[:MAX_NEXT_STEPS]
    return mapping
