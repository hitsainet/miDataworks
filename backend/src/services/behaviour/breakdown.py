"""The breakdown columns of a behaviour version (017 FR-017.51; FTID §10.1).

miStudio groups its breakdown by these columns (names not pinned: it takes them as parameters, BRD
OQ-10 (a)), so every role's published split keeps them (008's projection ships every user column)
and the manifest LISTS them: ``extensions.breakdown_columns`` (open ``extensions``; no contract
field). Listed, in the version's column order: FR-017.12's provenance columns, ``pair_id``,
``planned_*``, every ``sq_*``, ``deployed_truth``, ``label_conflict`` / ``_reason``, and the fact
columns the build's ``rule_set@1`` steps wrote (each pinned rule set's outputs with the step's
prefix, and ``<prefix>vetoed`` when it declares vetoes). Column names only: values are behaviour
data. A version that is not a behaviour build lists nothing (None: the key is left out).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

FIXED: tuple[str, ...] = (
    "behaviour_key",
    "definition_version",
    "genre",
    "generator",
    "generator_revision",
    "carrier_id",
    "placement",
    "form",
    "framing",
    "kind",
    "length_band",
    "pair_id",
    "deployed_truth",
    "label_conflict",
    "label_conflict_reason",
)
PREFIXES: tuple[str, ...] = ("sq_", "planned_")


def fact_columns(session: Session, version: Any) -> list[str]:
    """The columns the version's ``rule_set@1`` steps wrote, read from its recipe and the rule
    sets it carries (016's record)."""
    from ...models.recipe import RecipeBody
    from ..rules import carry

    recipe = session.get(RecipeBody, version.recipe_hash)
    steps = (recipe.body.get("steps") if recipe is not None else None) or []
    carried: dict[tuple[Any, Any], Any] = {
        (c.name, c.version): c for c in carry.carried(session, version) or []
    }
    out: list[str] = []
    for step in steps:
        if step.get("operator") != "rule_set":
            continue
        params = step.get("params") or {}
        found = carried.get((params.get("rule_set_name"), params.get("rule_set_version")))
        if found is None:
            continue
        document = json.loads(found.body_canonical)
        prefix = str(params.get("output_prefix") or "")
        out.extend(prefix + str(o["column"]) for o in document.get("outputs") or [])
        if document.get("vetoes"):
            out.append(prefix + "vetoed")
    return out


def breakdown_columns(session: Session, version: Any, columns: Sequence[str]) -> list[str] | None:
    """The listed columns among ``columns`` (the published ones), or None for a version that is
    not a behaviour build."""
    if version.behaviour_definition is None and version.behaviour_configuration is None:
        return None
    facts = set(fact_columns(session, version))
    return [c for c in columns if c in FIXED or c.startswith(PREFIXES) or c in facts]
