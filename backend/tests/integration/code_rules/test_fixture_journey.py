"""Fixture 2 through MCP alone (feature 018; FTASKS 14.7; FR-018.45, FR-018.48; FTDD §11.6).

An agent, through the MCP tools registered from the LIVE category registry (``tests/support/
mcp_live.py``: the real ``DataworksClient`` over the FastAPI app, the agent header on every call;
only the network is replaced): reads the runner contract, dry-runs the rule, saves it (pending),
the OPERATOR approves in the test (an operator-origin REST call; no agent can approve), saves a rule
set naming it, builds a version, reads the version's code-rule record, builds the publish files and
publishes privately to the fake Hub - with no sibling fake registered and both sibling URLs unset
(FR-018.48). The runner is the recording fake behind the real client; the build records the hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.config import get_settings
from src.runner_contract.hashing import code_sha256
from tests.integration.code_rules.conftest import count, live_tools
from tests.integration.code_rules.test_build_dispatch import recipe, rs_step, texts_for
from tests.integration.code_rules.test_travel import code_publisher  # noqa: F401 - fixture
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner
from tests.support.mcp_live import call, live_mcp
from tests.support.publish_fixtures import REPO, store_token
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name", "production_driver")
BEHAVIOUR = "fixture-gamma"
JOURNEY = (
    "dataworks_get_runner_contract",
    "dataworks_test_code_rule",
    "dataworks_save_code_rule",
    "dataworks_get_approval_status",
    "dataworks_save_rule_set",
    "dataworks_create_dataset",
    "dataworks_save_recipe",
    "dataworks_build_version",
    "dataworks_get_version",
    "dataworks_build_version_files",
    "dataworks_get_publish_build",
    "dataworks_publish_version",
)


def body(result: Any) -> Any:
    """A tool's answer as data (structured content, else the JSON text)."""
    if isinstance(result, dict | list):
        return result
    content = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if content:
        return content.get("result", content)
    blocks = getattr(result, "content", None) or result
    return json.loads(blocks[0].text)


async def test_fixture_two_through_mcp_alone_to_a_private_publish(
    client: httpx.AsyncClient,
    fake: FakeRunner,
    production_driver: Any,
    code_publisher: Any,  # noqa: F811
    data_dir: Path,
) -> None:
    settings = get_settings()
    assert settings.millm_base_url is None and settings.mistudio_base_url is None
    assert set(JOURNEY) <= await live_tools(), "every journey tool is in the live registry"
    await store_token(client)
    mcp, _ = live_mcp()
    f = fx.fixture(BEHAVIOUR)

    contract = body(await call(mcp, "dataworks_get_runner_contract", {}))
    assert contract["format"] == "midataworks.runner/v1" and contract["code_rules_enabled"]
    tried = body(
        await call(
            mcp, "dataworks_test_code_rule", {"document": f["document"], "cases": f["cases"]}
        )
    )
    assert all(c["passed"] for c in tried["cases"]), tried
    pending = body(
        await call(
            mcp,
            "dataworks_save_code_rule",
            {
                "name": f["name"],
                "behaviour_key": BEHAVIOUR,
                "document": f["document"],
                "cases": f["cases"],
            },
        )
    )
    assert pending["status"] == "pending", pending
    assert count("dw_config_versions", "kind = 'code_rule'") == 0
    approved = await client.post(f"/api/v1/approvals/{pending['approval_id']}/approve")
    assert approved.status_code == 200, approved.text  # the OPERATOR, never the agent
    status = body(
        await call(mcp, "dataworks_get_approval_status", {"approval_id": pending["approval_id"]})
    )
    assert status["status"] in ("approved", "executed"), status
    sha = code_sha256(f["document"])
    assert count("dw_code_rule_approvals", "code_sha256 = :s AND action = 'approve'", s=sha) == 1
    rule_set = body(
        await call(
            mcp, "dataworks_save_rule_set", {"document": fx.rule_set_document(BEHAVIOUR, sha)}
        )
    )
    assert rule_set["version"] == 1, rule_set
    dataset = body(
        await call(mcp, "dataworks_create_dataset", {"name": "journey", "target_type": "detector"})
    )
    recipe_out = body(
        await call(
            mcp,
            "dataworks_save_recipe",
            {"name": "journey-recipe", "body": recipe(rs_step(rule_set, use="label"))},
        )
    )
    rows = [{"text": t, "label": n % 2} for n, t in enumerate(texts_for(BEHAVIOUR))]
    source = make_source(data_dir, {"train": rows}, repo_id="t/journey")
    started = body(
        await call(
            mcp,
            "dataworks_build_version",
            {
                "dataset_id": dataset["id"],
                "recipe_revision_id": recipe_out["revision_id"],
                "inputs": [{"kind": "source", "source_id": source}],
                "seed": 7,
                "column_roles": {"text": "content"},
            },
        )
    )
    assert production_driver.run(started["job_id"]) == "completed"
    job = (await client.get(f"/api/v1/jobs/{started['job_id']}")).json()
    version_id = job["result"]["version_id"]
    version = body(await call(mcp, "dataworks_get_version", {"version_id": version_id}))
    (carried,) = version["code_rules"]["code_rules"]
    assert carried["code_sha256"] == sha and carried["approval_state"] == "approved"
    assert count("dw_code_rule_uses", "code_sha256 = :s", s=sha) == 1  # the build recorded it

    build_job = body(
        await call(
            mcp,
            "dataworks_build_version_files",
            {"version_id": version_id, "label_column": "label"},
        )
    )
    code_publisher.run_publish_tasks()
    build = body(
        await call(mcp, "dataworks_get_publish_build", {"build_id": build_job["build_id"]})
    )
    assert build["status"] == "completed", build
    published = body(
        await call(
            mcp,
            "dataworks_publish_version",
            {"version_id": version_id, "build_id": build["id"], "repo_id": REPO},
        )
    )
    if published.get("status") == "pending":  # an agent's push waits for the operator
        decided = await client.post(f"/api/v1/approvals/{published['approval_id']}/approve")
        assert decided.status_code == 200, decided.text
    code_publisher.run_publish_tasks()
    files = code_publisher.hub.head_files(REPO)
    assert f"code_rules/{sha}.json" in files and f"code_rules/{sha}.cases.json" in files
    manifest = json.loads(files["midataworks-dataset-version.json"])
    assert (
        manifest["extensions"]["midataworks.code-rules/v1"]["code_rules"][0]["code_sha256"] == sha
    )
