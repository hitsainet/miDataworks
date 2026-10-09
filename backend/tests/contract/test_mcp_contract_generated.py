"""``docs/mcp-contract.md`` equals what the live registry generates (FR-010.36; FTASKS 8.4)."""

from __future__ import annotations

import re

from src.mcp_server.contract import CONTRACT_PATH, generate_contract
from tests.support.mcp_ledger import EXPECTED_CALLS, full


def test_the_committed_contract_is_the_generated_one() -> None:
    assert CONTRACT_PATH.exists(), "docs/mcp-contract.md is missing; regenerate it"
    assert CONTRACT_PATH.read_text(encoding="utf-8") == generate_contract(), (
        "docs/mcp-contract.md is stale. Regenerate: cd backend && python -c "
        '"from src.mcp_server.contract import write_contract; write_contract()"'
    )


def test_every_tool_row_names_the_route_its_caller_test_asserts() -> None:
    """The contract's endpoint column and the caller harness agree, tool by tool."""
    text = generate_contract()
    for tool, (method, path, _, _) in EXPECTED_CALLS.items():
        row = next(line for line in text.splitlines() if line.startswith(f"| `{tool}` |"))
        templates = re.findall(r"`(GET|POST|PUT|PATCH|DELETE) ([^`]+)`", row)
        concrete = full(path)
        assert any(
            m == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", t), concrete)
            for m, t in templates
        ), (tool, templates, method, concrete)


def test_the_rest_contract_section_names_what_proxies_rely_on() -> None:
    text = generate_contract()
    for item in (
        "## REST contract for proxies",
        "X-Dataworks-Agent",
        "AGENT_CANNOT_DECIDE",
        "GET /api/v1/openapi.json",
        "GET /api/health",
        "gate_target_write",
    ):
        assert item in text, item
