"""A carried code rule's visibility floor reads source licences as check C-1 does (FR-018.41;
018 Review R3).

The floor is the strictest of the rule's ``derived_from.sources`` and its cases' source versions. A
source licence that does not PERMIT redistribution (the class of an unknown or absent licence is
``private_only``) keeps the rule private, exactly as C-1 refuses a public push of such a source's
rows; a licence that forbids it reads ``forbids_redistribution``. A reference that names no stored
version or source - including one that is not an id at all - fails closed (``private_only``) and
never reaches the database as a malformed id. Before R3 an unlisted licence read ``none`` and a
non-id reference crashed the publish.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.services.code_rules import carry
from tests.support.version_fixtures import make_source

pytestmark = pytest.mark.usefixtures("operator_name")


def _floor(*references: str, case_sources: tuple[str, ...] = ()) -> tuple[str, Any]:
    document = {"derived_from": {"sources": list(references), "statement": "fixture"}}
    cases = [
        {"id": f"c{i}", "source": {"source_version_id": s}} for i, s in enumerate(case_sources)
    ]
    with sync_session_factory()() as session:
        return carry.visibility_floor(session, document, cases)


async def test_a_permitting_licence_sets_no_floor(data_dir: Path) -> None:
    permitted = make_source(data_dir, {"train": [{"text": "a"}]}, licence="cc-by-2.0")
    assert _floor(permitted) == ("none", ())


async def test_a_licence_that_does_not_permit_redistribution_keeps_the_rule_private(
    data_dir: Path,
) -> None:
    unlisted = make_source(data_dir, {"train": [{"text": "a"}]}, licence="some-custom-terms")
    absent = make_source(data_dir, {"train": [{"text": "a"}]}, licence=None)
    for source in (unlisted, absent):
        floor, basis = _floor(source)
        assert floor == "private_only", source
        assert basis == ({"reference": source, "floor": "private_only"},)


async def test_a_licence_annotated_as_forbidding_redistribution_is_the_strictest(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    forbidden = make_source(data_dir, {"train": [{"text": "a"}]}, licence="cc-by-2.0")
    unlisted = make_source(data_dir, {"train": [{"text": "a"}]}, licence="some-custom-terms")
    annotated = await client.post(
        f"/api/v1/sources/{forbidden}/annotations",
        json={"kind": "terms", "redistribution": "forbids", "value": {}, "reason": "read them"},
    )
    assert annotated.status_code in (200, 201), annotated.text
    floor, basis = _floor(unlisted, case_sources=(forbidden,))
    assert floor == "forbids_redistribution"
    assert {b["reference"]: b["floor"] for b in basis} == {
        unlisted: "private_only",
        forbidden: "forbids_redistribution",
    }


@pytest.mark.parametrize(
    "reference", ["hf:org/data", "not an id", "00000000-0000-4000-8000-00000000d1d0"]
)
async def test_a_reference_that_names_nothing_fails_closed_without_a_database_error(
    reference: str,
) -> None:
    assert _floor(reference) == (
        "private_only",
        ({"reference": reference, "floor": "private_only"},),
    )
