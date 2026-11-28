"""A tool error as a client receives it from the production server (022 FR-022.11).

``build_server``'s ``DataworksMCPServer`` returns every tool failure as ``CallToolResult(isError=True)``
whose text starts ``<code>: <one-line message>``, instead of raising ``ToolError`` out of
``call_tool``. Tests that drove a failure through ``mcp.call_tool`` read the result here.
"""

from __future__ import annotations

import re
from typing import Any


def expect_tool_error(result: Any, pattern: str) -> str:
    """Assert ``result`` is an error result whose text matches ``pattern``; return the text."""
    assert getattr(result, "is_error", False), f"expected a tool error, got {result!r}"
    text: str = result.content[0].text
    assert re.search(pattern, text), (pattern, text)
    assert not text.startswith("Error executing tool"), text
    return text
