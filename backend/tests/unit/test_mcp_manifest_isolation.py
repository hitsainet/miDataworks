"""The MCP pod has no database URL, Redis URL or data volume (ADR-016; FR-010.6; FTID 8.2).

miStudio's MCP pod lacked a database while one tool imported a session factory; every check ran in
a process that had a database, and the tool raised on its first production call. Here the absence
is the design, so the manifest is read and the absence asserted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

MANIFEST = Path(__file__).resolve().parents[3] / "k8s/base/mcp.yaml"
FORBIDDEN_ENV = {"DATABASE_URL", "REDIS_URL", "CELERY_BROKER_URL", "POSTGRES_PASSWORD"}


def deployment() -> dict[str, Any]:
    docs = [d for d in yaml.safe_load_all(MANIFEST.read_text()) if d]
    (dep,) = [
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "midataworks-mcp"
    ]
    return dep


def test_the_mcp_container_has_no_database_or_redis_env() -> None:
    spec = deployment()["spec"]["template"]["spec"]
    for container in spec["containers"] + spec.get("initContainers", []):
        names = {e["name"] for e in container.get("env", [])}
        assert not names & FORBIDDEN_ENV, (container["name"], sorted(names & FORBIDDEN_ENV))
        assert not container.get("envFrom"), "envFrom could import the backend's whole config"


def test_the_mcp_pod_mounts_no_volume() -> None:
    spec = deployment()["spec"]["template"]["spec"]
    assert not spec.get("volumes"), spec.get("volumes")
    for container in spec["containers"]:
        assert not container.get("volumeMounts"), container.get("volumeMounts")


def test_the_mcp_pod_reaches_the_backend_over_http_with_a_bearer_token() -> None:
    (container,) = deployment()["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e for e in container["env"]}
    assert env["SERVICE_TYPE"]["value"] == "mcp"
    assert env["DATAWORKS_API_URL"]["value"].startswith("http://midataworks-backend:")
    assert env["MCP_AUTH_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "midataworks-secrets",
        "key": "MCP_BEARER_TOKEN",
    }
    assert env["MCP_AGENT_IDENTITY"]["value"] == "agent:dataworks-mcp"
