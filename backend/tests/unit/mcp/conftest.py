"""Every tool result in the MCP unit suite conforms to the tool's published ``outputSchema`` (022 FR-022.23).

An autouse fixture wraps the SDK's ``ToolManager.call_tool`` (the class, so every server and the
harness are covered, whatever name a test imported). A ``CallToolResult`` carrying
``structuredContent`` is validated against the tool's schema; a raw result (the harness calls tools
without the server's result pass) is validated as the production pass would structure it.
``VALIDATED`` counts the checks, and ``test_output_schemas.py`` asserts the count moves.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import jsonschema
import pytest
from mcp.server.mcpserver.tools.tool_manager import ToolManager
from mcp_types import CallToolResult

from src.mcp_server.server import load_output_schemas

VALIDATED: list[str] = []
_SCHEMAS = load_output_schemas()


@pytest.fixture(autouse=True)
def _results_conform_to_their_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = ToolManager.call_tool

    async def checked(self: ToolManager, name: str, arguments: Any, *args: Any, **kw: Any) -> Any:
        result = await original(self, name, arguments, *args, **kw)
        schema = _SCHEMAS.get(name)
        if schema is None:
            return result
        if isinstance(result, CallToolResult):
            if result.is_error:
                return result
            assert result.structured_content is not None, f"{name} has a schema and no structure"
            data = result.structured_content
        else:
            data = result if isinstance(result, dict) else {"result": result}
        jsonschema.validate(data, schema)
        VALIDATED.append(name)
        return result

    monkeypatch.setattr(ToolManager, "call_tool", checked)
    yield
