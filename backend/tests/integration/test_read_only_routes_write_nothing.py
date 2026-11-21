"""Every non-GET route a read-only tool calls writes nothing (022 FR-022.17; 022-D14, D17).

For each ``READ_ONLY_ROUTES`` entry (``tests/support/mcp_calls.py``): seed what the route needs,
snapshot every table's row count AND content hash (approvals and jobs included), the data volume's
files with their content hashes and the Celery tasks sent, call the route as the operator and again
as an agent, and compare. Counts alone cannot see an UPDATE: R3 review fix (OR3-022-2), 2026-11-16. A route
that answers 4xx for the fixture proved nothing and fails. A route that writes moves its tool to
write in ``mcp_server/annotations.py``, with the reason.

A Celery task a route sends is a write unless it is in ``READ_ONLY_TASKS``: a request-reply task
that reads the Hub and answers, creating no row, job or file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, sync_session_factory
from tests.integration.test_version_build import build, setup, src
from tests.support.mcp_calls import READ_ONLY_ROUTES
from tests.support.source_fixtures import HfEnv, hf_env
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver", "hf_env"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}

#: Celery tasks a read-only route may send: request-reply, no row, job or file. Empty since the
#: Hub search became a write (suite A37; R3 review fix (OR3-022-3), 2026-11-16): a task is a job.
READ_ONLY_TASKS: dict[str, str] = {}

ROWS = [
    {"text": f"a line of text number {i}", "label": "yes" if i % 2 else "no"} for i in range(40)
]


@dataclass
class Seeded:
    source_id: str
    dataset_id: str
    revision_id: str
    version_id: str
    sent: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    def fields(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "dataset_id": self.dataset_id,
            "revision_id": self.revision_id,
            "version_id": self.version_id,
            **self.extra,
        }


def tables() -> dict[str, tuple[int, str]]:
    """Each table's row count and an md5 of its rows in a stable order: an INSERT, a DELETE and
    an UPDATE of any column all change it (R3 review fix (OR3-022-2), 2026-11-16)."""
    import src.models  # noqa: F401 - every table on the metadata

    with sync_session_factory()() as db:
        out: dict[str, tuple[int, str]] = {}
        for table in Base.metadata.sorted_tables:
            name = db.get_bind().dialect.identifier_preparer.quote(table.name)
            count, digest = db.execute(
                text(
                    "SELECT count(*), md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), ''))"
                    f" FROM {name} t"  # noqa: S608 - a name from the metadata, quoted
                )
            ).one()
            out[table.name] = (int(count), str(digest))
        return out


def files(root: Path) -> set[tuple[str, int, str]]:
    """Every file under the data volume with its size and sha256: a rewrite in place shows."""
    out: set[tuple[str, int, str]] = set()
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = Path(dirpath) / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out.add((str(path.relative_to(root)), path.stat().st_size, digest))
    return out


Call = Callable[[httpx.AsyncClient, Seeded, dict[str, str] | None], Awaitable[httpx.Response]]


def _post(path: str, payload: dict[str, Any]) -> Call:
    async def call(
        client: httpx.AsyncClient, seeded: Seeded, headers: dict[str, str] | None
    ) -> httpx.Response:
        return await client.post(_fill(path, seeded), json=_fill(payload, seeded), headers=headers)

    return call


def _fill(value: Any, seeded: Seeded) -> Any:
    if isinstance(value, str):
        # Only the seeded names are placeholders: a rule set's regex keeps its own braces.
        known = seeded.fields()
        return re.sub(r"\{(\w+)\}", lambda m: known.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _fill(v, seeded) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, seeded) for v in value]
    return value


RECIPE = body(("stub_drop_short", {"min_len": 8}))
#: A recipe of one native operator; its version is the installed one (set by the fixture).
NATIVE: dict[str, Any] = {
    "format": "dw.recipe/v1",
    "steps": [
        {
            "operator": "cell_balancer",
            "version": "",
            "params": {"column": "label", "label_column": "label"},
        }
    ],
}
RULE_SET = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/behaviours/fixture-alpha/rule_set.json"
    ).read_text()
)

Prepare = Callable[[httpx.AsyncClient, Seeded], Awaitable[None]]


async def _clone_recipe(client: httpx.AsyncClient, seeded: Seeded) -> None:
    """A recipe and its clone, for the difference route (made BEFORE the snapshot)."""
    recipes = (await client.get("/api/v1/recipes")).json()
    items = recipes["items"] if isinstance(recipes, dict) else recipes
    recipe_id = items[0]["id"]
    clone = await client.post(f"/api/v1/recipes/{recipe_id}/clone", json={"name": "humor-clone"})
    assert clone.status_code in (200, 201), clone.text
    seeded.extra.update(recipe_id=recipe_id, clone_id=clone.json()["id"])


#: (METHOD, template) -> what to make before the snapshot (if anything).
PREPARE: dict[tuple[str, str], Prepare] = {
    ("POST", "/api/v1/differences"): _clone_recipe,
}

#: (METHOD, template) -> how to call it against the seeded objects.
CASES: dict[tuple[str, str], Call] = {
    ("POST", "/api/v1/differences"): _post(
        "/api/v1/differences", {"kind": "recipe", "a": "{recipe_id}", "b": "{clone_id}"}
    ),
    ("POST", "/api/v1/sources/{source_id}/profile"): _post(
        "/api/v1/sources/{source_id}/profile", {"value_columns": ["label"]}
    ),
    ("POST", "/api/v1/versions/plan"): _post(
        "/api/v1/versions/plan",
        {
            "dataset_id": "{dataset_id}",
            "recipe_revision_id": "{native_revision_id}",
            "inputs": [{"kind": "source", "source_id": "{source_id}"}],
        },
    ),
    ("POST", "/api/v1/versions/{version_id}/splits/{split}/download-url"): _post(
        "/api/v1/versions/{version_id}/splits/train/download-url", {}
    ),
    ("POST", "/api/v1/recipes/validate"): _post("/api/v1/recipes/validate", {"body": NATIVE}),
    ("POST", "/api/v1/operators/upgrade-plan"): _post(
        "/api/v1/operators/upgrade-plan", {"recipe_body": NATIVE}
    ),
    ("POST", "/api/v1/operators/{name}/{version}/validate"): _post(
        "/api/v1/operators/cell_balancer/{balancer_version}/validate",
        {"params": {"column": "label", "label_column": "label"}},
    ),
    ("POST", "/api/v1/rule-sets/validate"): _post(
        "/api/v1/rule-sets/validate", {"document": {"format": "not-a-rule-set"}}
    ),
    ("POST", "/api/v1/rule-sets/preview"): _post(
        "/api/v1/rule-sets/preview", {"document": RULE_SET, "text": "a line with 123-45-6789 in it"}
    ),
    ("POST", "/api/v1/versions/{version_id}/trl-validation"): _post(
        "/api/v1/versions/{version_id}/trl-validation", {"target_type": "sft"}
    ),
}


#: Routes not yet proved here, each with what its fixture needs (022 FTASKS 2.2, open). A route
#: in ``READ_ONLY_ROUTES`` with neither a case nor an entry here fails the test.
NOT_YET_PROVED: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/calibration-sets/preview"): "a version with a label column to map",
    (
        "POST",
        "/api/v1/calibration-sets/{set_id}/label-agreement",
    ): "a calibration set with human labels",
    ("POST", "/api/v1/code-rules/test"): "the fake runner of tests/support/fake_runner.py",
    (
        "POST",
        "/api/v1/detector-sets/{set_id}/leakage-repair/plan",
    ): "a detector set with a leaking role",
    ("POST", "/api/v1/generation-runs/plan"): "the generation fixtures (endpoints, a template)",
    ("POST", "/api/v1/minimal-pair-chains/plan"): "a generation template and a rubric",
    ("POST", "/api/v1/results/{kind}/{object_id}/download-url"): "a census or proof result",
    ("POST", "/api/v1/generation-runs/independence-check"): "configured generation and judge roles",
    ("POST", "/api/v1/steering-settings/compare"): "the generation role and a fake miServer",
}


@pytest.fixture
async def seeded(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    hf_env: HfEnv,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Seeded:
    from src.core.celery_app import celery_app

    source_id = make_source(data_dir, {"train": ROWS})
    dataset_id, revision_id = await setup(client, RECIPE)
    version = await build(client, driver, dataset_id, revision_id, [src(source_id)], seed=1)
    out = Seeded(source_id, dataset_id, revision_id, version["id"])
    # The plan and validation routes read the NATIVE operator registry (the build above used the
    # driver's stubs), as tests/integration/test_build_plan.py does.
    from src.operators import registry as registry_module
    from src.operators.registry import OperatorRegistry
    from src.services import operator_port

    native = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(registry_module, "current", lambda: native)
    monkeypatch.setattr(operator_port, "_registry", native)
    balancer = native.current_version("cell_balancer")
    NATIVE["steps"][0]["version"] = balancer
    recipe = await client.post("/api/v1/recipes", json={"name": "native-recipe", "body": NATIVE})
    assert recipe.status_code == 201, recipe.text
    out.extra.update(
        native_revision_id=recipe.json()["head_revision_id"], balancer_version=balancer
    )
    hf_env.mock.overrides["/api/datasets?"] = (200, [])
    forward = hf_env.send_task

    def record(name: str, args: list[Any] | None = None, **kw: Any) -> Any:
        out.sent.append(name)
        return forward(name, args, **kw)

    monkeypatch.setattr(celery_app, "send_task", record)
    return out


@pytest.mark.parametrize("route", sorted(READ_ONLY_ROUTES), ids=lambda r: f"{r[0]} {r[1]}")
async def test_a_read_only_route_writes_nothing(
    route: tuple[str, str], client: httpx.AsyncClient, seeded: Seeded, data_dir: Path
) -> None:
    call = CASES.get(route)
    if call is None:
        assert route in NOT_YET_PROVED, f"{route}: no case proves it writes nothing"
        pytest.skip(f"NOT PROVED YET: {route[0]} {route[1]} needs {NOT_YET_PROVED[route]}")
    if route in PREPARE:
        await PREPARE[route](client, seeded)
    for who, headers in (("operator", None), ("agent", AGENT)):
        before_rows, before_files = tables(), files(data_dir)
        seeded.sent.clear()
        response = await call(client, seeded, headers)
        assert 200 <= response.status_code < 300, (who, response.status_code, response.text)
        after_rows = tables()
        changed = {
            t: (before_rows[t], after_rows[t])
            for t in after_rows
            if after_rows[t] != before_rows[t]
        }
        # The agent's own request is recorded by the origin middleware after the answer: that row
        # is the audit of the call, not a write the route made.
        changed.pop("dw_agent_requests", None)
        assert not changed, f"{route} as {who} changed rows (count, md5): {changed}"
        assert files(data_dir) == before_files, f"{route} as {who} wrote files"
        writes = [t for t in seeded.sent if t not in READ_ONLY_TASKS]
        assert not writes, f"{route} as {who} sent tasks {writes}"


def test_the_cases_name_live_read_only_routes() -> None:
    assert set(CASES) <= set(READ_ONLY_ROUTES), sorted(set(CASES) - set(READ_ONLY_ROUTES))
    assert not set(CASES) & set(NOT_YET_PROVED)
    assert set(NOT_YET_PROVED) <= set(READ_ONLY_ROUTES)
    assert len(CASES) >= 10, "a proved route lost its case"
