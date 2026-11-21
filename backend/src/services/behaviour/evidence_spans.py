"""The evidence-span columns of a behaviour version (ENH-006 G39; operator decision 2026-11-09).

miWorkbench recommends a probe's pooling rule from where the evidence sits in each positive row: a local
pattern (an SSN in a long text) wants ``max`` and ``rolling_mean_max``, a whole-text behaviour ``mean``.
It reads that from an OPTIONAL span column on the view (``span_column`` on miWorkbench's registration body):
per row, the half-open character offsets (Unicode code points) of the evidence in the row's text. Without
one it recommends nothing and refuses nothing.

The column is a rule set's own span output: an output whose value is ``{"spans": <expression>}``
(``midataworks-rule-set-v1.json``: "spans = canonical JSON of the carried spans, code points"), written by a
``rule_set@1`` step with its ``output_prefix`` over the step's ``text_column`` (the SSN pack's
``r_ssn_spans`` over ``text``). This lists those columns, read from the recipe and the rule sets the
version carries, so the manifest names them (``extensions.evidence_spans``: open ``extensions``, no schema
change) and the send can name one per role.

Left out, never guessed:

- a rule set with a signal that reads the ELEMENT (``reads: element`` or ``candidates: {element: true}``):
  its spans may be offsets in the element column, not in the text;
- a column the build did not publish.

The send then declares one only on a role whose ``input_column`` is the step's ``text_column``: the
offsets index that text and no other (``detector_sets/span_column.py``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import Session

#: The manifest's ``extensions`` key.
LISTING_KEY = "evidence_spans"
UNIT = "code_points"


def _reads_element(document: dict[str, Any]) -> bool:
    for signal in document.get("signals") or []:
        if signal.get("reads") == "element":
            return True
        candidates = signal.get("candidates") or {}
        if isinstance(candidates, dict) and candidates.get("element"):
            return True
    return False


def span_outputs(session: Session, version: Any) -> list[dict[str, Any]]:
    """Every span output the version's ``rule_set@1`` steps wrote, as listed entries, in step order;
    an entry from a rule set that reads the element says so (``reads_element``)."""
    from ...models.recipe import RecipeBody
    from ..rules import carry

    recipe = session.get(RecipeBody, version.recipe_hash)
    steps = (recipe.body.get("steps") if recipe is not None else None) or []
    carried: dict[tuple[Any, Any], Any] = {
        (c.name, c.version): c for c in carry.carried(session, version) or []
    }
    out: list[dict[str, Any]] = []
    for step in steps:
        if step.get("operator") != "rule_set":
            continue
        params = step.get("params") or {}
        found = carried.get((params.get("rule_set_name"), params.get("rule_set_version")))
        if found is None:
            continue
        document = json.loads(found.body_canonical)
        prefix = str(params.get("output_prefix") or "")
        element = _reads_element(document)
        for output in document.get("outputs") or []:
            if "spans" not in (output.get("value") or {}):
                continue
            entry: dict[str, Any] = {
                "column": prefix + str(output["column"]),
                "text_column": str(params.get("text_column") or ""),
                "unit": UNIT,
                "rule_set": f"{params.get('rule_set_name')}@{params.get('rule_set_version')}",
            }
            if element:
                entry["reads_element"] = True
            out.append(entry)
    return out


def evidence_span_columns(
    session: Session, version: Any, columns: Sequence[str]
) -> list[dict[str, Any]] | None:
    """The listed span columns among ``columns`` (the published ones), or None when there are none
    (the key is left out) or the version is not a behaviour build."""
    if version.behaviour_definition is None and version.behaviour_configuration is None:
        return None
    published = set(columns)
    listed = [
        e
        for e in span_outputs(session, version)
        if e["column"] in published and not e.get("reads_element")
    ]
    return listed or None
