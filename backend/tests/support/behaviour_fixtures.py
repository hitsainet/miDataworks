"""017 fixtures: the two test-only behaviours' carrier plans (TD-017.39; FTASKS 0.9 skeleton).

``fixture-alpha`` (slot carriers for an insertable element) and ``fixture-beta`` (instructed
carriers, no element) live under ``tests/fixtures/behaviours/`` beside 016's rule sets. Their
vocabularies share no value, so code that works for one by accident (a hard-coded list) fails the
other. A capability tested on one fixture alone is not done.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa

from tests.fixtures import humor_pool as hp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "behaviours"
BEHAVIOURS = ("fixture-alpha", "fixture-beta")
INDEX_ROLES = {"idx": "content"}


def plan_document(key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / key / "plan.json").read_text(encoding="utf-8"))
    return data


def definition(key: str, version: int | None = 1, *, verified: bool = True) -> dict[str, Any]:
    """A recorded definition as dataworks_resolve_behaviour's ``record`` gives it."""
    return {
        "key": key,
        "definition_version": version,
        "positive_meaning": f"{key} shows the behaviour" if verified else None,
        "verified": verified,
        "source": "catalogue" if verified else "supplied",
        "read_at": "2026-10-24T00:00:00+00:00",
    }


def plan_params(key: str, rows: int, **over: Any) -> dict[str, Any]:
    """``carrier_plan@1`` parameters for a fixture behaviour (JSON text, as a recipe holds them)."""
    doc = plan_document(key)
    params: dict[str, Any] = {
        "definition": json.dumps(definition(key)),
        "vocabulary": json.dumps(doc["vocabulary"]),
        "shares": json.dumps(doc["shares"]),
        "rows": rows,
        "templated_share": doc["templated_share"],
        "templated_ceiling": doc["templated_ceiling"],
    }
    params.update(over)
    return params


def index_table(rows: int, *, split: str = "train") -> pa.Table:
    """An index source's rows: ``idx`` as content, as ``POST /sources/index`` makes them."""
    return hp.with_system_columns(
        [{"idx": i} for i in range(rows)], split=split, source_id="src-index", content=("idx",)
    )
