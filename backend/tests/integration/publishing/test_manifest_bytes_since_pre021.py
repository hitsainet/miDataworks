"""Since the pre-021 bytes: push 1 changed nothing shipped; push 3 only adds (021 FTASKS 0.6, 2.2, 9.3;
FTID I2; R1A-D4).

The golden bytes were captured at the 021 handoff commit (``895dde3``, before any 021 code) from the
manifest sites on the NON-WSP fixture ``golden-plain`` (built with ``split@1.2.0``): the draft
(``drafts.built_document``, read through the handoff manifest before a publish), the publish job's
in-repository file (``publish_job.run_publish``), the stored published manifest
(``with_publication``, read back through the handoff manifest), a miForge-set export
(``export_service.export_manifest``) and a reward bundle (``reward_bundle.build``).

Frozen inputs: every ``uuid.uuid4`` (the source id, hence its commit and every ``_dw_origin``) is a
fixed sequence. What a patch cannot freeze is replaced by a placeholder before comparing, never
dropped: the prefixed row ids (``secrets.token_hex``, ``core/ids.py``) by ``<id-N>`` in order of first
appearance, timestamps (PostgreSQL ``now()`` and the sites' clocks) by ``<ts>``, and the fake Hub's
random commit oid by ``<hub-commit>`` (OVN-wsp-dataworks-1). Keys, structure and every content value are compared byte for byte.

Recapture (only at the handoff commit): ``MIDATAWORKS_CAPTURE_GOLDEN_PRE021=1``.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.canonical_json import canonical_json
from src.core.storage import export_dir
from src.services.publishing.manifest_builder import MANIFEST_FILE
from tests.integration.publishing.test_generated_held_out_caveat import publisher
from tests.integration.test_directed_held_out import driver, run
from tests.integration.test_version_build import src
from tests.support.operator_build_driver import RealDriver
from tests.support.publish_fixtures import (
    REPO,
    PublishDriver,
    completed_build,
    publish_and_run,
    store_token,
)
from tests.support.version_fixtures import make_source

__all__ = ["driver", "publisher"]

GOLDEN = Path(__file__).resolve().parents[2] / "unit/contract/fixtures/golden_pre021"
CAPTURE = os.environ.get("MIDATAWORKS_CAPTURE_GOLDEN_PRE021") == "1"

#: golden-plain: a detector dataset of short texts, split by ``split@1.2.0`` (no WSP parameter)
ROWS = [
    {
        "text": f"golden plain row {i} {'pun' if i % 2 else 'fact'}",
        "prompt": f"Say something {i}",
        "label": "pos" if i % 2 else "neg",
    }
    for i in range(16)
]
SPLIT_120 = {
    "operator": "split",
    "version": "1.2.0",
    "params": {
        "split_names": ["train", "test"],
        "split_fractions": [0.75, 0.25],
        "held_out": ["test"],
    },
}
BINARY_RUBRIC = {
    "style": "binary",
    "messages": [
        {
            "role": "user",
            "content": "Prompt: {prompt}\nAnswer: {completion}\nIs it a pun? VERDICT: yes or no.",
        }
    ],
    "input_fields": ["prompt", "completion"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}

_ID = re.compile(r"^[a-z]+_[0-9a-f]{24}$")
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


@pytest.fixture
def frozen_uuid4(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    counter = iter(range(1, 1_000_000))
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=0x0210_0000 + next(counter)))
    yield


def normalise(document: Any) -> Any:
    """Placeholders for what no patch can freeze; everything else as it was (module docstring)."""
    seen: dict[str, str] = {}

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(value[k]) for k in sorted(value)}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            if _ID.match(value):
                return seen.setdefault(value, f"<id-{len(seen) + 1}>")
            if _TS.match(value):
                return "<ts>"
            for raw, token in seen.items():
                value = value.replace(raw, token)
        return value

    return walk(document)


def golden_bytes(document: Any) -> bytes:
    out = normalise(document)
    if isinstance(out.get("publication"), dict) and "commit" in out["publication"]:
        out["publication"]["commit"] = "<hub-commit>"  # the fake Hub's commit oid is random
    return canonical_json(out) + b"\n"


async def golden_plain(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, declared: str | None = None
) -> str:
    source = make_source(
        data_dir, {"train": ROWS}, repo_id="t/golden-plain", text_columns=("text", "prompt")
    )
    if declared is None:
        out = await run(client, driver, [SPLIT_120], [src(source)], "golden-plain")
    else:
        from src.core.database import sync_session_factory
        from src.models import Job
        from tests.integration.test_version_build import request, setup

        recipe = {"format": "dw.recipe/v1", "steps": [SPLIT_120], "sensitivity": declared}
        ds, rev = await setup(client, recipe, name="golden-plain")
        response = await request(
            client, ds, rev, [src(source)], seed=7, column_roles={"text": "content"}
        )
        assert response.status_code == 202, response.text
        driver.run(response.json()["job_id"])
        with sync_session_factory()() as db:
            job = db.get(Job, response.json()["job_id"])
            assert job is not None and job.result is not None
            out = {"status": job.status, **job.result}
    assert out["status"] == "completed", out
    return str(out["version_id"])


async def site_documents(
    client: httpx.AsyncClient,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    declared: str | None = None,
) -> dict[str, Any]:
    """Each manifest site's document for golden-plain, in the order a user reaches them."""
    await store_token(client)
    version_id = await golden_plain(client, driver, data_dir, declared)
    build = await completed_build(client, publisher, version_id)
    docs: dict[str, Any] = {}
    docs["draft"] = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    pub = await publish_and_run(client, publisher, version_id, build["id"])
    assert pub["status"] == "published", pub["error"]
    docs["publish_in_repository"] = json.loads(publisher.hub.head_files(REPO)[MANIFEST_FILE])
    docs["published"] = (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).json()
    exported = await client.post(
        "/api/v1/exports",
        json={
            "target": "miforge_set",
            "miforge_set_kind": "corpus",
            "version_id": version_id,
            "label_column": "label",
        },
    )
    assert exported.status_code == 202, exported.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{exported.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    docs["export_miforge_set"] = json.loads((export_dir(row["id"]) / MANIFEST_FILE).read_bytes())
    rubric = await client.post(
        "/api/v1/rubrics", json={"name": "golden/judge", "body": BINARY_RUBRIC}
    )
    assert rubric.status_code == 201, rubric.text
    bundled = await client.post(
        "/api/v1/exports",
        json={
            "target": "reward_bundle",
            "version_id": version_id,
            "rubric_id": rubric.json()["id"],
            "judge_model_id": "judge-7b",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
            "seed": 11,
        },
    )
    assert bundled.status_code == 202, bundled.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{bundled.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    docs["export_reward_bundle"] = json.loads((export_dir(row["id"]) / MANIFEST_FILE).read_bytes())
    return docs


SITES = (
    "draft",
    "publish_in_repository",
    "published",
    "export_miforge_set",
    "export_reward_bundle",
)


async def test_push3_adds_exactly_the_two_keys_to_the_pre021_bytes(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    frozen_uuid4: None,
) -> None:
    """FR-021.4, FR-021.21 (FTASKS 9.3): on the non-WSP, undeclared golden-plain fixture, every
    site's manifest is the pre-021 manifest plus EXACTLY ``sensitivity: null``, ``wsp: null`` on
    each rows split and ``extensions.sensitivity_basis``; nothing else moved. (Push 1 compared the
    bytes unchanged; push 3 proves the change is only additive.)"""
    docs = await site_documents(client, driver, publisher, data_dir)
    assert set(docs) == set(SITES)
    if CAPTURE:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for site, document in docs.items():
            (GOLDEN / f"{site}.json").write_bytes(golden_bytes(document))
        # FR-021.3's fixture: a real stored publish, unnormalised (pre021_manifest.json)
        (GOLDEN.parent / "pre021_manifest.json").write_bytes(
            canonical_json(docs["published"]) + b"\n"
        )
        pytest.skip("captured the pre-021 goldens; rerun without the capture switch")
    for site in SITES:
        document = json.loads(json.dumps(docs[site]))
        assert "sensitivity" in document and document.pop("sensitivity") is None, site
        basis = document["extensions"].pop("sensitivity_basis")
        assert basis["floor"] is None and basis["declared"] is None, site
        assert basis["raised_by"] == [], site
        for split in (document.get("content") or {}).get("splits") or []:
            assert "wsp" in split and split.pop("wsp") is None, site
        assert golden_bytes(document) == (GOLDEN / f"{site}.json").read_bytes(), site


@pytest.mark.parametrize("declared", ["sensitive", "not_sensitive"])
async def test_every_site_states_sensitivity(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    declared: str,
) -> None:
    """FR-021.21 (FTASKS 9.2; M-26): the draft, the publish (in the repository and as stored),
    the export and the reward bundle each state the version's value and its basis. A sensitive
    version publishes privately (the default here)."""
    docs = await site_documents(client, driver, publisher, data_dir, declared)
    for site in SITES:
        assert docs[site]["sensitivity"] == declared, site
        basis = docs[site]["extensions"]["sensitivity_basis"]
        assert basis["floor"] == declared and basis["declared"] == declared, site
        assert basis["raised_by"] == [], site


def test_every_site_passes_sensitivity() -> None:
    """FR-021.21 (AST, beside the behavioural test above): every ``build_document`` call under
    ``src/`` passes ``sensitivity=`` and ``sensitivity_basis=`` from ``sensitivity.effective``,
    and the four sites are exactly the known four."""
    import ast

    root = Path(__file__).resolve().parents[3] / "src"
    calls: dict[str, list[set[str]]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "build_document"
            ):
                rel = str(path.relative_to(root))
                calls.setdefault(rel, []).append({k.arg for k in node.keywords if k.arg})
    assert sorted(calls) == [
        "services/exports/export_service.py",
        "services/exports/reward_bundle.py",
        "services/publishing/drafts.py",
        "services/publishing/publish_job.py",
    ]
    for rel, found in calls.items():
        assert len(found) == 1, rel
        assert {"sensitivity", "sensitivity_basis"} <= found[0], rel
