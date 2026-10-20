# Origin: miStudio (Onegaishimas/miStudio) backend/src/mcp_server/config.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: MCPSettings, warn-and-drop of unknown categories, the
# non-prefixed backend URL. Changed: eleven categories, all on by default; an `agent_identity`
# validated against the REST header pattern; no steering or miLLM settings (010 FTID section 3.1).
"""MCP server configuration (environment prefix ``MCP_``; 010 FTDD section 6.1).

**Two lists that must agree.** ``VALID_CATEGORIES`` is a second view of the registry in
``tools/__init__.py`` and cannot be derived here (``tools`` imports this module). A category in the
registry but missing here is refused at start and looks exactly like the tools not existing, so
``tests/unit/test_mcp_category_lists_agree.py`` pins the two together. miStudio's copy of this
comment cited a test that did not exist; this one does.

The MCP pod has no database URL, Redis URL or data volume (ADR-016), so nothing here may read the
backend's ``Settings``.
"""

from __future__ import annotations

import logging
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Every category this build serves (010 FTID section 3.1). Keep in step with
#: ``tools.CATEGORY_MODULES``; the list-agreement test fails otherwise.
VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "core",
        "datasets",
        "operators",
        "curation",
        "settings",
        "labeling",
        "calibration",
        "review",
        "generation",
        "exports",
        "detector_sets",
    }
)

#: All eleven on by default (FR-010.11). The deployment sets the same list explicitly.
DEFAULT_CATEGORIES = ",".join(sorted(VALID_CATEGORIES))

#: The REST layer's header pattern (``core/agent_origin.py``), repeated because this package must
#: not import the backend; ``tests/unit/mcp/test_client.py`` compares the two by AST.
AGENT_IDENTITY_PATTERN = r"^agent:[a-z0-9][a-z0-9-]{0,47}$"


class MCPSettings(BaseSettings):
    """Runtime configuration for the miDataworks MCP server."""

    model_config = SettingsConfigDict(env_prefix="MCP_", extra="ignore")

    auth_token: str = Field(default="", description="Bearer token required on the HTTP transport")
    agent_identity: str = Field(
        default="agent:dataworks-mcp",
        pattern=AGENT_IDENTITY_PATTERN,
        description="Sent as X-Dataworks-Agent on every backend request (P-12: one identity)",
    )
    allow_anonymous: bool = Field(
        default=False, description="Permit start without a token (stdio transport only)"
    )
    #: Every interface inside the pod; the bearer token gates every request but /health.
    host: str = Field(default="0.0.0.0", description="Bind host")  # noqa: S104
    port: int = Field(default=8765)
    tool_categories: str = Field(default=DEFAULT_CATEGORIES)

    @property
    def api_url(self) -> str:
        """Backend base URL. Not ``MCP_``-prefixed, as miStudio's ``MISTUDIO_API_URL``."""
        return os.environ.get("DATAWORKS_API_URL", "http://localhost:8000").rstrip("/")

    def requested_categories(self) -> set[str]:
        """Exactly what ``MCP_TOOL_CATEGORIES`` asked for, recognised or not."""
        return {c.strip() for c in self.tool_categories.split(",") if c.strip()}

    def unknown_categories(self) -> set[str]:
        """Requested names this build does not recognise (reported on ``/health``)."""
        return self.requested_categories() - VALID_CATEGORIES

    def enabled_categories(self) -> set[str]:
        """The recognised categories; unknown names are WARNED AND DROPPED, never honoured.

        miStudio raised here once, and every category addition became a crash-loop window: the
        manifest syncs in minutes and the image takes about nine, so a new manifest meets an image
        whose list predates it. A typo in the deployed manifest is caught in CI instead
        (``test_reachability.py`` reads ``k8s/base/mcp.yaml``).
        """
        requested = self.requested_categories()
        unknown = requested - VALID_CATEGORIES
        if unknown:
            logger.warning(
                "Ignoring unknown MCP tool categories: %s (valid: %s). Expected briefly while a "
                "manifest change leads its image; if it persists, those tools are OFF.",
                sorted(unknown),
                sorted(VALID_CATEGORIES),
            )
        return requested & VALID_CATEGORIES
