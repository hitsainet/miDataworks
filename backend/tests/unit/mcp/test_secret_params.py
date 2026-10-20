"""Every credential-like parameter is in SECRET_PARAMS, and only where allowed (7.4; 3.4)."""

from __future__ import annotations

import re

from src.mcp_server.context import SECRET_PARAMS, SECRET_PARAMS_OUTSIDE_SETTINGS
from src.mcp_server.server import tool_names_declared
from src.mcp_server.tools import CATEGORY_MODULES
from tests.support.mcp_harness import build_harness

CREDENTIAL = re.compile(r"(token|secret|password|api_key|key)$")

#: Parameters the pattern catches that carry no credential, each with the reason.
NOT_CREDENTIALS = {
    "row_key": "A row's SHA-256 identity (ADR-005), not a secret.",
    "key": "The NAME of a setting to clear (dataworks_delete_setting), never its value.",
}


def _params() -> dict[str, set[str]]:
    mcp, _ = build_harness()
    return {
        t.name: set(t.parameters.get("properties", {}))
        for t in mcp._tool_manager.list_tools()  # noqa: SLF001
    }


def test_every_credential_like_parameter_is_registered() -> None:
    missing = {
        (tool, param)
        for tool, params in _params().items()
        for param in params
        if CREDENTIAL.search(param)
        and param not in NOT_CREDENTIALS
        and param not in SECRET_PARAMS.get(tool, frozenset())
    }
    assert not missing, f"credential-like parameters not in SECRET_PARAMS: {sorted(missing)}"


def test_every_registered_secret_names_a_real_parameter() -> None:
    params = _params()
    for tool, names in SECRET_PARAMS.items():
        assert tool in params, tool
        assert names <= params[tool], (tool, names - params[tool])


def test_only_settings_tools_take_a_secret_unless_excepted_with_a_reason() -> None:
    settings_tools = {n for m in CATEGORY_MODULES["settings"] for n in tool_names_declared(m)}
    outside = set(SECRET_PARAMS) - settings_tools
    assert outside == set(SECRET_PARAMS_OUTSIDE_SETTINGS), outside
    assert all(len(r) > 20 for r in SECRET_PARAMS_OUTSIDE_SETTINGS.values())
