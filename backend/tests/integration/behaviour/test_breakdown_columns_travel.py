"""The breakdown columns travel (017 FTASKS 10.11; FR-017.51).

A behaviour version built through the real chain for BOTH fixtures (two generators' carriers,
``insert_element@1`` for fixture-alpha, ``rule_set@1``, ``derive_labels@1``), then 008's real
publish build: every FR-017.12 column, the fact columns the rule set wrote, ``deployed_truth``,
``label_conflict`` and every ``sq_*`` are in every split file the build writes (what each role's
published split is), and the card draft's manifest AND the manifest pushed to the Hub list them in
``extensions.breakdown_columns``. Controls M31: a column dropped from the projection, or from the
listing, or either caller's listing removed, turns this red.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from src.core.storage import publish_dir
from src.models.publish import PublishBuild
from src.services.publishing import drafts
from tests.integration.behaviour.test_label_build import PROVENANCE, labelled_version
from tests.support.generation_fixtures import Gen
from tests.support.label_fixtures import labels, rule_set_document
from tests.support.operator_build_driver import RealDriver
from tests.support.publish_fixtures import (
    REPO,
    PublishDriver,
    completed_build,
    publish_and_run,
    publisher,
    store_token,
)

__all__ = ["publisher"]


def expected_columns(key: str) -> set[str]:
    doc = labels(key)
    prefix = doc["output_prefix"]
    rule_set = rule_set_document(key)
    facts = {prefix + o["column"] for o in rule_set["outputs"]}
    if rule_set.get("vetoes"):
        facts.add(prefix + "vetoed")
    from tests.support.behaviour_fixtures import plan_document

    sub = {f"sq_{q['name']}" for q in plan_document(key)["vocabulary"].get("sub_questions", [])}
    return {*PROVENANCE, *facts, *sub, "deployed_truth", "label_conflict"}


@pytest.mark.parametrize("key", ["fixture-alpha", "fixture-beta"])
async def test_every_breakdown_column_is_in_every_split_and_listed(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    key: str,
) -> None:
    _, version = await labelled_version(client, gen, driver, data_dir, key)
    label_column = labels(key)["label_column"]
    build = await completed_build(client, publisher, version["id"], label_column=label_column)
    wanted = expected_columns(key)
    root = publish_dir(build["id"])
    assert build["files"]
    for f in build["files"]:  # every split the build writes: each role's published split
        names = set(pq.read_schema(root / str(f["path"])).names)
        missing = wanted - names
        assert not missing, (f["path"], sorted(missing))
    with sync_session_factory()() as db:
        built = db.get(PublishBuild, build["id"])
        assert built is not None
        document, _ = drafts.built_document(db, version["id"], built, None)
    listed: list[str] = document["extensions"]["breakdown_columns"]
    assert wanted <= set(listed), sorted(wanted - set(listed))
    assert label_column not in listed and "text" not in listed  # the label and text are not
    assert len(listed) == len(set(listed))
    # 017 FR-017.11 (FTID §10.1): the definition the version was built against travels too
    assert document["extensions"]["behaviour_definition"] == version["behaviour_definition"]
    assert document["extensions"]["behaviour_definition"]["key"] == key
    # the manifest the publish job writes to the Hub lists the same columns (its own caller)
    await store_token(client)
    pub = await publish_and_run(client, publisher, version["id"], build["id"])
    assert pub["status"] == "published", pub["error"]
    pushed = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    assert pushed["extensions"]["breakdown_columns"] == listed
    assert pushed["extensions"]["behaviour_definition"] == version["behaviour_definition"]


def test_a_version_that_is_not_a_behaviour_build_lists_nothing() -> None:
    from types import SimpleNamespace

    from src.services.behaviour.breakdown import breakdown_columns

    plain = SimpleNamespace(behaviour_definition=None, behaviour_configuration=None)
    assert breakdown_columns(None, plain, ["genre", "sq_x"]) is None  # type: ignore[arg-type]
