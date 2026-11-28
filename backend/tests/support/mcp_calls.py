"""What a tool's calls are, read from a replay and the LIVE OpenAPI document (022 FR-022.17, .26).

``templated`` maps a recorded call (a path relative to ``/api/v1``, as ``mcp_server/client.py``
sends it) onto the one live OpenAPI template it reaches. It is written apart from
``scripts/generate_mcp_call_map.py``'s matcher on purpose: the pin test compares the two, so a
matcher bug cannot agree with itself (memory ``derive-from-the-registry-not-the-source``).

``READ_ONLY_ROUTES`` is every non-``GET`` route a read-only tool may call, with the reason it
writes nothing. ``tests/integration/test_read_only_routes_write_nothing.py`` proves each against
the real backend; ``tests/unit/mcp/test_read_only_calls.py`` proves no read tool calls anything
else.
"""

from __future__ import annotations

import re
from functools import cache
from typing import Any

from tests.support.mcp_harness import RecordingClient
from tests.support.mcp_ledger import full

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

#: (METHOD, OpenAPI template) -> why the route writes nothing although it is not a GET.
READ_ONLY_ROUTES: dict[tuple[str, str], str] = {
    (
        "POST",
        "/api/v1/calibration-sets/{set_id}/label-agreement",
    ): "compares stored human labels with a run's labels; stores nothing",
    ("POST", "/api/v1/code-rules/test"): "runs a document in the runner against given text; "
    "stores nothing (a save is POST /code-rules)",
    ("POST", "/api/v1/sources/{source_id}/profile"): "whole-split statistics of an imported "
    "source, computed and answered",
    ("POST", "/api/v1/versions/plan"): "validity, the labelling-window count and caps of a "
    "recipe; nothing is built",
    (
        "POST",
        "/api/v1/versions/{version_id}/splits/{split}/download-url",
    ): "signs an expiring URL; the token is the authorisation, nothing is stored",
    ("POST", "/api/v1/differences"): "compares two stored documents",
    ("POST", "/api/v1/results/{kind}/{object_id}/download-url"): "signs an expiring URL",
    ("POST", "/api/v1/recipes/validate"): "validates a recipe document; saves nothing",
    (
        "POST",
        "/api/v1/detector-sets/{set_id}/leakage-repair/plan",
    ): "a plan; nothing is repaired",
    ("POST", "/api/v1/minimal-pair-chains/plan"): "a plan; no chain is started",
    ("POST", "/api/v1/generation-runs/plan"): "a plan; no run is started",
    ("POST", "/api/v1/steering-settings/compare"): "compares stored settings",
    (
        "POST",
        "/api/v1/generation-runs/independence-check",
    ): "checks two endpoints' independence from their stored configuration",
    (
        "POST",
        "/api/v1/operators/{name}/{version}/validate",
    ): "validates parameters against an operator's schema",
    ("POST", "/api/v1/operators/upgrade-plan"): "a plan; no recipe is changed",
    ("POST", "/api/v1/calibration-sets/preview"): "previews a mapping; no set is made",
    ("POST", "/api/v1/rule-sets/validate"): "validates a rule-set document; saves nothing",
    ("POST", "/api/v1/rule-sets/preview"): "evaluates a rule set over given text; saves nothing",
    ("POST", "/api/v1/versions/{version_id}/trl-validation"): "checks a version against TRL's "
    "format; stores nothing",
}


@cache
def openapi_paths() -> dict[str, Any]:
    from src.main import fastapi_app

    paths: dict[str, Any] = fastapi_app.openapi()["paths"]
    return paths


def _pattern(template: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(template)) + "$")


def templated(method: str, path: str) -> str:
    """The live template a recorded ``(method, path)`` reaches; fewest parameters wins."""
    concrete = full(path)
    hits = sorted(
        (template.count("{"), template)
        for template, operations in openapi_paths().items()
        if method.lower() in operations and _pattern(template).match(concrete)
    )
    assert hits, f"{method} {concrete} reaches no live route"
    assert len(hits) == 1 or hits[0][0] < hits[1][0], f"{method} {concrete} is ambiguous: {hits}"
    return hits[0][1]


class ScriptedClient(RecordingClient):
    """A recorder that answers from a script: each call takes the first answer whose
    ``(method, path prefix)`` matches, in order; an ``Exception`` answer is raised. Unmatched
    calls answer the default response."""

    def __init__(self, script: list[tuple[str, str, Any]], response: Any = None) -> None:
        super().__init__(response)
        self.script = list(script)

    async def _record(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        for i, (m, prefix, answer) in enumerate(self.script):
            if m == method and path.startswith(prefix):
                del self.script[i]
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return self.response


@cache
def _shared_harness() -> tuple[Any, RecordingClient]:
    from tests.support.mcp_harness import build_harness

    return build_harness()


def replay(
    tool: str, kwargs: dict[str, Any] | None = None
) -> list[tuple[str, str, dict[str, Any]]]:
    """Call ``tool`` once through one shared harness (default response) with its ledger
    arguments; return the calls it sent. One harness for every replay: building ~230 servers is
    the slow part, and a tool keeps no state between calls."""
    from tests.support.mcp_harness import call_tool
    from tests.support.mcp_ledger import EXPECTED_CALLS

    mcp, client = _shared_harness()
    client.calls = []
    call_tool(mcp, tool, EXPECTED_CALLS[tool][2] if kwargs is None else kwargs)
    calls, client.calls = client.calls, []
    return calls
