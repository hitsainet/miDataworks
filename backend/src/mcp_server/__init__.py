"""miDataworks MCP server (feature 010; ADR-016).

Runs as its own process and Deployment (``python -m src.mcp_server``) with no database URL, Redis
URL or data volume. Every tool reaches the backend over HTTP through ``client.DataworksClient``,
which stamps the agent header the REST approval gate keys on (ADR-013).
"""
