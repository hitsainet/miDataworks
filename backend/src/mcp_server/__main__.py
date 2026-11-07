# Origin: miStudio (Onegaishimas/miStudio) backend/src/mcp_server/__main__.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: argparse `--stdio`, uvicorn over the streamable-HTTP app,
# closing the backend clients on stop. Changed: the SDK logger at WARNING so tool arguments are not
# logged at debug (010 FTID section 3.5); the bearer middleware is always added on HTTP (inside
# `build_http_app`), because `build_server` has already refused an HTTP start without a token.
"""Entry point: ``python -m src.mcp_server`` (streamable HTTP) or ``--stdio``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import MCPSettings
from .server import build_http_app, build_server, wrap_tool_with_audit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# The SDK logs call arguments at debug; a secret argument must never reach a log (FR-010.42).
logging.getLogger("mcp").setLevel(logging.WARNING)
logger = logging.getLogger("mcp_server")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="miDataworks MCP server")
    parser.add_argument("--stdio", action="store_true", help="Run on stdio (local development)")
    args = parser.parse_args(argv)

    settings = MCPSettings()
    mcp, _client = build_server(settings, stdio=args.stdio)
    wrap_tool_with_audit(mcp)

    if args.stdio:
        logger.info("Starting the miDataworks MCP server on stdio")
        mcp.run(transport="stdio")
        return 0

    import uvicorn

    app = build_http_app(mcp, settings)
    logger.info(
        "Starting the miDataworks MCP server on %s:%s (backend %s, identity %s)",
        settings.host,
        settings.port,
        settings.api_url,
        settings.agent_identity,
    )
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    finally:
        asyncio.run(mcp.close_backend_clients())  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    sys.exit(main())
