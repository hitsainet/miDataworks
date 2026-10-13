"""What every tool module is handed besides the client (010 FTID section 3.4).

``SECRET_PARAMS`` names, per tool, the arguments that carry a credential. The audit wrapper
replaces them with ``"<redacted>"`` BEFORE it digests the arguments, so a log line never carries a
value from which the secret could be recovered (FR-010.5, FR-010.42). Only ``settings`` tools take
a secret; ``tests/unit/mcp/test_secret_params.py`` fails if any credential-like parameter is
missing from this map or if a listed tool lives outside that category.

``GATED_TOOLS`` names the tools whose REST route waits for the operator when an agent calls it,
and the approval action. It is a statement for descriptions and the generated contract, never a
control: the gate lives in REST (C6). ``tests/unit/test_mcp_parity.py`` checks every entry against
the ``x-approval-action`` marker the live OpenAPI document carries for the route the tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from .config import MCPSettings
from .health_gate import HealthGate

#: Dependency sets a tool declares (FR-010.13). Every route reads PostgreSQL; a route that starts
#: a job also needs Redis; a route that reads or writes Parquet needs the data volume.
READ = ("backend", "postgres")
JOB = ("backend", "postgres", "redis")
FILES = ("backend", "postgres", "data_volume")
JOB_FILES = ("backend", "postgres", "redis", "data_volume")


@dataclass(frozen=True)
class ToolContext:
    settings: MCPSettings
    gate: HealthGate


SECRET_PARAMS: dict[str, frozenset[str]] = {
    "dataworks_set_hf_token": frozenset({"token"}),
    "dataworks_set_endpoint_key": frozenset({"api_key"}),
    # Feature 001's HF preview and import take an optional access token (FR-001.35); they are in
    # the `datasets` category, so the category rule has an explicit exception for them below.
    "dataworks_preview_hf_dataset": frozenset({"access_token"}),
    "dataworks_import_hf_dataset": frozenset({"access_token"}),
}

#: Tools outside `settings` that may take a secret, with the reason.
SECRET_PARAMS_OUTSIDE_SETTINGS: dict[str, str] = {
    "dataworks_preview_hf_dataset": "A gated dataset needs a token to preview (FR-001.35).",
    "dataworks_import_hf_dataset": "A gated dataset needs a token to import (FR-001.35).",
}

#: tool name -> approval action (010 FTID section 3.8 "The 15 gated tools", as far as served).
GATED_TOOLS: dict[str, str] = {
    "dataworks_set_hf_token": "secret_write",
    "dataworks_set_endpoint_key": "secret_write",
    "dataworks_delete_setting": "secret_write",
    "dataworks_preview_hf_dataset": "secret_write",
    "dataworks_import_hf_dataset": "secret_write",
    "dataworks_delete_version": "version_delete",
    "dataworks_build_version": "agent_label_rows",
    "dataworks_start_label_run": "agent_label_rows",
    "dataworks_annotate_source": "source_annotate",
    "dataworks_publish_version": "hub_push",
    "dataworks_push_card": "hub_push",
    "dataworks_set_calibration_target": "gate_target_write",
    "dataworks_create_reproduction_link": "gate_target_write",
    "dataworks_send_detector_set": "hub_push",
}

# --- argument shapes, validated by the MCP SDK BEFORE the gate or any request -----------------

#: An identifier that goes into a URL path: no slash, no "..", so an argument cannot re-route a
#: request to another endpoint.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
#: A Hugging Face model or repository ID, which carries one slash (``owner/name``).
REPO_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"
#: A model ID as 008's model-terms routes take it (an HF ID, or a bare local name).
MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}(/[A-Za-z0-9][A-Za-z0-9_.-]{0,95})?$"

#: Page and page size as the backend's list routes take them (1-based; at most 200 per page).
Page = Annotated[int, Field(ge=1, le=1_000_000, description="Page number, from 1.")]
Limit = Annotated[int, Field(ge=1, le=200, description="Items per page, at most 200.")]
