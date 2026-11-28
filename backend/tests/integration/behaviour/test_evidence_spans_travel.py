"""The evidence-span columns travel in the manifest (ENH-006 G39; operator decision 2026-11-09).

A behaviour version built through the real chain (``rule_set@1`` over ``text`` with prefix ``r_``):
fixture-alpha's rule set declares a span output (``fa_spans``, ``{"spans": "valid_card"}``) and
fixture-beta's none. The card draft's manifest AND the manifest pushed to the Hub list the column under
``extensions.evidence_spans``, with the text column it indexes, so the send can declare it and a reader of
the published dataset knows what it is. Removing either caller's argument, or the listing, turns this red.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models.publish import PublishBuild
from src.services.publishing import drafts
from tests.integration.behaviour.test_label_build import labelled_version
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


def expected(key: str) -> list[dict[str, str]] | None:
    doc = labels(key)
    rule_set = rule_set_document(key)
    spans = [o["column"] for o in rule_set["outputs"] if "spans" in o["value"]]
    if not spans:
        return None
    return [
        {
            "column": doc["output_prefix"] + column,
            "text_column": "text",
            "unit": "code_points",
            "rule_set": f"{rule_set['name']}@1",
        }
        for column in spans
    ]


@pytest.mark.parametrize("key", ["fixture-alpha", "fixture-beta"])
async def test_the_span_outputs_are_listed_with_the_text_they_index(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    publisher: PublishDriver,
    data_dir: Path,
    key: str,
) -> None:
    _, version = await labelled_version(client, gen, driver, data_dir, key)
    build = await completed_build(
        client, publisher, version["id"], label_column=labels(key)["label_column"]
    )
    with sync_session_factory()() as db:
        built = db.get(PublishBuild, build["id"])
        assert built is not None
        document, _ = drafts.built_document(db, version["id"], built, None)
        published = {str(c["name"]) for c in built.columns or []}
    wanted = expected(key)
    listed = document["extensions"].get("evidence_spans")
    if wanted is None:
        assert (
            key == "fixture-beta" and listed is None
        ), "a rule set without a span output lists none"
        return
    assert listed is not None and [e["rule_set"].split("@")[0] for e in listed] == [
        rule_set_document(key)["name"]
    ]
    assert [{k: e[k] for k in ("column", "text_column", "unit")} for e in listed] == [
        {k: e[k] for k in ("column", "text_column", "unit")} for e in wanted
    ]
    assert {e["column"] for e in listed} <= published, "only a column the build published"
    await store_token(client)
    pub = await publish_and_run(client, publisher, version["id"], build["id"])
    assert pub["status"] == "published", pub["error"]
    pushed = json.loads(publisher.hub.head_files(REPO)["midataworks-dataset-version.json"])
    assert pushed["extensions"]["evidence_spans"] == listed


def test_a_version_that_is_not_a_behaviour_build_lists_nothing() -> None:
    from types import SimpleNamespace

    from src.services.behaviour.evidence_spans import evidence_span_columns

    plain = SimpleNamespace(behaviour_definition=None, behaviour_configuration=None)
    assert evidence_span_columns(None, plain, ["r_fa_spans"]) is None  # type: ignore[arg-type]
