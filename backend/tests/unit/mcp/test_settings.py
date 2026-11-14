"""MCP settings: unknown categories warned and dropped, the identity validated (FTASKS 6.8)."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from src.mcp_server.config import MCPSettings


def test_an_unknown_category_is_warned_and_dropped(caplog: pytest.LogCaptureFixture) -> None:
    settings = MCPSettings(tool_categories="core,datasetz", auth_token="x")
    with caplog.at_level(logging.WARNING):
        assert settings.enabled_categories() == {"core"}
    assert "datasetz" in caplog.text
    assert settings.unknown_categories() == {"datasetz"}


@pytest.mark.parametrize(
    "identity", ["dataworks-mcp", "agent:", "agent:UPPER", "agent:-x", "agent:" + "a" * 49, ""]
)
def test_an_invalid_identity_is_rejected_at_start(identity: str) -> None:
    with pytest.raises(ValidationError):
        MCPSettings(agent_identity=identity)


def test_the_default_identity_and_backend_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAWORKS_API_URL", raising=False)
    settings = MCPSettings()
    assert settings.agent_identity == "agent:dataworks-mcp"
    assert settings.api_url == "http://localhost:8000"
    monkeypatch.setenv("DATAWORKS_API_URL", "http://midataworks-backend:8000/")
    assert MCPSettings().api_url == "http://midataworks-backend:8000"
