"""``slot_siblings@1``: counterfactuals made by code (017 FR-017.34; FTID §3.6, §16 X-40).

A **generator** over slot carriers that ``insert_element@1`` filled. For each carrier holding its
element (``boundary_kind = slot``, ``element_present``, a kind that is not a declared decoy, and
a kind listed in ``kinds`` when given) it ADDS one sibling per declared variant: the same carrier
text with ONLY the element span ``[ins_start, ins_end)`` replaced, so every code point outside the
span is identical (a framing phrase sits outside the span and stays). No judge is needed.

Variants (``variants``, JSON text: ``[{kind, ref?, column?, as_kind?}]``):

- ``empty``: nothing in the slot (``element_present = false``).
- ``decoy``: ``ref`` names a decoy id of the pinned rule set, declared as a decoy kind in the
  vocabulary; the sibling's value is drawn (seeded per row) from the decoy's list and its ``kind``
  becomes the decoy kind.
- ``vetoed``: ``ref`` names a veto id of the pinned rule set; the value is the row's ``column``
  (the vetoed form of the element, written before this step) and the sibling's ``kind`` becomes
  ``as_kind`` (a declared kind). A decoy or vetoed value is rendered by the carrier's ``form`` when
  the rule set declares that rendering, as ``insert_element@1`` would have inserted it.
- ``rendering``: ``ref`` names a rendering of the pinned rule set and a declared form; the row's
  ``element_column`` is rendered (016's ``rules/renderings``) and ``element_recovered`` records the
  normaliser's verdict, as ``insert_element@1`` does.

A sibling whose span would hold the carrier's own text is not made (counted in the report).

Siblings are NEW rows (``_dw_origin = generated``, parent = the carrier, FTDD §4.3), so every
held-out guard stays strict over them. Every output row gets ``pair_id`` (the row's
``carrier_id``, else its own key) and ``sibling_variant`` (null on the carriers), so a split
grouped by ``pair_id`` keeps a carrier with its siblings and D-4 refuses one that separates them.

**Order.** Siblings are labelled by the declared rules like every other row: the step runs AFTER
``insert_element@1`` and BEFORE ``rule_set@1`` and ``derive_labels@1``. An input that already holds
derived labels (``deployed_truth``) or the pinned rule set's outputs is refused
``siblings_after_labels`` (a copied label or fact would be a fabricated one). Refusals and events
carry keys, columns and declared ids, never row text. No behaviour is named here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....rules import renderings as rendering_rules
from ....rules.rule_set import GenerateList, Rendering, RuleSetDocument
from ....services.behaviour.vocabulary import Vocabulary, VocabularyError, parse_vocabulary
from ...context import RunContext
from ...errors import StepFailed
from ...manifest import ColumnSpec
from ...protocol import OperatorResult
from ..curation.common import manifest
from .common import (
    NEXT_RULE_SET,
    NEXT_VALIDATE,
    PIN_SCHEMA,
    deferred,
    json_param,
    pinned,
    refused,
    row_random,
    rule_set_of,
    vocabulary_of,
)
from .insert_element import _normaliser_text

NAME = "slot_siblings"
KINDS = ("empty", "decoy", "vetoed", "rendering")
PAIR = "pair_id"
VARIANT = "sibling_variant"
DERIVED = "deployed_truth"
PARENTS = "_dw_parent_keys"
SLOT = "slot"


def parse_variants(params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The declared variants, shape-checked (the pinned rule set is checked in the worker)."""
    raw = json_param(params, "variants", None)
    if not isinstance(raw, list) or not raw:
        raise VocabularyError(
            "vocabulary_invalid",
            "variants is a non-empty JSON list of {kind, ref?, column?, as_kind?}.",
            {"field": "variants"},
        )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise VocabularyError(
                "vocabulary_invalid",
                f"variants[{i}].kind is one of {list(KINDS)}.",
                {"field": f"variants[{i}].kind"},
            )
        allowed = {"kind", "ref"} | ({"column", "as_kind"} if item["kind"] == "vetoed" else set())
        extra = sorted(set(item) - allowed)
        if extra:
            raise VocabularyError(
                "vocabulary_invalid",
                f"variants[{i}] ({item['kind']}) takes no {extra}.",
                {"field": f"variants[{i}]"},
            )
        needs = {"empty": (), "decoy": ("ref",), "rendering": ("ref",)}.get(
            item["kind"], ("ref", "column", "as_kind")
        )
        for name in needs:
            if not isinstance(item.get(name), str) or not item[name]:
                raise VocabularyError(
                    "vocabulary_invalid",
                    f"variants[{i}] ({item['kind']}) needs {name!r}.",
                    {"field": f"variants[{i}].{name}"},
                )
        if item["kind"] == "empty" and item.get("ref") is not None:
            raise VocabularyError(
                "vocabulary_invalid",
                f"variants[{i}] (empty) takes no ref.",
                {"field": f"variants[{i}].ref"},
            )
        label = variant_label(item)
        if label in seen:
            raise VocabularyError(
                "vocabulary_invalid",
                f"variants[{i}] repeats {label!r}.",
                {"field": f"variants[{i}]"},
            )
        seen.add(label)
        out.append(dict(item))
    return out


def variant_label(variant: Mapping[str, Any]) -> str:
    kind = str(variant["kind"])
    return kind if kind == "empty" else f"{kind}:{variant['ref']}"


def _check_vocabulary(variants: list[dict[str, Any]], vocabulary: Vocabulary) -> None:
    for i, v in enumerate(variants):
        if v["kind"] == "decoy" and v["ref"] not in vocabulary.decoys:
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"variants[{i}]: decoy {v['ref']!r} is not a declared decoy kind "
                f"(declared: {list(vocabulary.decoys)}).",
                {"field": f"variants[{i}].ref", "value": v["ref"]},
            )
        if v["kind"] == "vetoed" and v["as_kind"] not in vocabulary.values("kind"):
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"variants[{i}]: as_kind {v['as_kind']!r} is not a declared kind.",
                {"field": f"variants[{i}].as_kind", "value": v["as_kind"]},
            )
        if v["kind"] == "rendering" and v["ref"] not in vocabulary.values("form"):
            raise VocabularyError(
                "vocabulary_value_undeclared",
                f"variants[{i}]: rendering {v['ref']!r} is not a declared form.",
                {"field": f"variants[{i}].ref", "value": v["ref"]},
            )


def check_sibling_params(params: Mapping[str, Any]) -> list[str]:
    """Cross-parameter checks at validation (an ``"@input"`` vocabulary waits for the build)."""
    try:
        variants = parse_variants(params)
        if not deferred(params.get("vocabulary")):
            _check_vocabulary(variants, parse_vocabulary(params.get("vocabulary")))
    except VocabularyError as exc:
        return [exc.problem("/variants")]
    if any(v["kind"] != "empty" for v in variants) and not pinned(params):
        return [
            "/rule_set_name: params_invalid: a decoy, vetoed or rendering variant reads the "
            "pinned rule set; pin the one the rule_set@1 step pins."
        ]
    if any(v["kind"] == "rendering" for v in variants) and not params.get("element_column"):
        return [
            "/element_column: params_invalid: a rendering variant renders the canonical element: "
            "name its column."
        ]
    return []


def _resolve(document: RuleSetDocument | None, variant: dict[str, Any]) -> Any:
    """The rule-set object a variant names (decoy, veto or rendering), refused when absent."""
    kind = variant["kind"]
    if kind == "empty":
        return None
    named: dict[str, Any] = {}
    if document is not None:
        if kind == "decoy":
            named = {d.id: d for d in document.decoys}
        elif kind == "vetoed":
            named = {v.id: v for v in document.vetoes}
        else:
            named = {r.rendering: r for r in document.renderings}
    found = named.get(str(variant["ref"]))
    if found is None:
        raise StepFailed(
            "sibling_variant_undeclared",
            f"Variant {variant_label(variant)!r} names no {kind} of "
            + ("an unpinned step" if document is None else f"rule set {document.name}")
            + ". Pin the rule set that declares it.",
            {"variant": variant_label(variant), "next": NEXT_RULE_SET},
        )
    if kind == "decoy" and not isinstance(found.generator, GenerateList):
        raise StepFailed(
            "decoy_not_runnable",
            f"Decoy {found.id!r} is not generated by a list, which this build cannot run yet.",
            {"decoy": found.id, "next": NEXT_RULE_SET},
        )
    return found


def _rendered(rendering: Rendering | None, element: str) -> tuple[str, bool]:
    """``element`` rendered (and the normaliser's verdict), or as given when no rendering."""
    if rendering is None or not element:
        return element, bool(element)
    spec = rendering.model_dump(by_alias=True)
    try:
        rendered = rendering_rules.render(spec, element)
        return rendered, rendering_rules.normalise(spec, rendered) == element
    except rendering_rules.RenderingNotRunnable as exc:
        raise StepFailed(
            "rendering_not_runnable",
            f"Rendering {rendering.rendering!r} cannot run in a build: {exc}.",
            {"rendering": rendering.rendering, "next": NEXT_RULE_SET},
        ) from None


def _band(vocabulary: Vocabulary, length: int) -> str | None:
    for band in vocabulary.length_bands:
        if length >= band.min_chars and (band.max_chars is None or length < band.max_chars):
            return band.name
    return None


class SlotSiblings:
    manifest = manifest(
        NAME,
        "generator",
        "Adds code-made counterfactuals of each slot carrier holding its element: the same text "
        "with only the element span replaced (nothing, a declared decoy, a vetoed form or another "
        "rendering). Siblings share pair_id with their carrier. Run it after insert_element and "
        "before rule_set and derive_labels, so siblings are labelled by the declared rules.",
        params={
            "variants": {
                "type": "string",
                "minLength": 2,
                "title": "Variants (JSON)",
                "x-widget": "textarea",
                "x-hint": '[{"kind": "empty"}, {"kind": "decoy", "ref": "<decoy id>"}, {"kind": '
                '"vetoed", "ref": "<veto id>", "column": "<vetoed value column>", "as_kind": '
                '"<kind>"}, {"kind": "rendering", "ref": "<rendering slug>"}]',
            },
            "vocabulary": {
                "type": "string",
                "minLength": 2,
                "title": "Vocabulary",
                "x-widget": "textarea",
                "x-hint": '"@input" reads the input version\'s recorded vocabulary.',
            },
            "text_column": {
                "type": "string",
                "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$",
                "default": "text",
                "title": "Text column",
            },
            "element_column": {
                "type": "string",
                "minLength": 1,
                "title": "Element column",
                "x-hint": "The canonical element per row; a rendering variant renders it.",
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "uniqueItems": True,
                "title": "Carrier kinds given siblings",
                "x-hint": "Default: every declared kind that is not a decoy.",
            },
            **PIN_SCHEMA,
        },
        required=("variants", "vocabulary"),
        output_columns=(
            ColumnSpec(name=PAIR, type="string", required=False, role="metadata"),
            ColumnSpec(name=VARIANT, type="string", required=False, role="metadata"),
        ),
    )

    def check_params(self, params: Mapping[str, Any]) -> list[str]:
        return check_sibling_params(params)

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        reads = [
            str(params.get("text_column") or "text"),
            "ins_start",
            "ins_end",
            "boundary_kind",
            "element_present",
            "kind",
        ]
        if params.get("element_column"):
            reads.append(str(params["element_column"]))
        return reads, [PAIR, VARIANT], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        try:
            vocabulary = vocabulary_of(params)
            variants = parse_variants(params)
            _check_vocabulary(variants, vocabulary)
        except VocabularyError as exc:
            raise refused(exc) from None
        document = rule_set_of(params, self.manifest.ref_text)
        resolved = [(v, _resolve(document, v)) for v in variants]
        by_slug = {} if document is None else {r.rendering: r for r in document.renderings}
        text_column = str(params.get("text_column") or "text")
        element_column = params.get("element_column")
        names = batch.schema.names
        needed = [text_column, "ins_start", "ins_end", "boundary_kind", "element_present", "kind"]
        needed += [str(element_column)] if any(v["kind"] == "rendering" for v in variants) else []
        needed += [str(v["column"]) for v in variants if v["kind"] == "vetoed"]
        for column in needed:
            if column not in names:
                raise StepFailed(
                    "column_not_found",
                    f"slot_siblings@1 reads {column!r}, which the input does not have: run "
                    "insert_element@1 (slot mode) before it.",
                    {"column": column, "next": NEXT_VALIDATE},
                )
        stale = [DERIVED] if DERIVED in names else []
        if document is not None:
            outputs = [o.column for o in document.outputs] + ["vetoed"]
            stale += [n for n in names if any(n.endswith(o) and n != o for o in outputs)]
            stale += [n for n in names if n in outputs]
        if stale:
            raise StepFailed(
                "siblings_after_labels",
                f"The input already holds {sorted(set(stale))}: a sibling would copy a label or "
                "fact decided for its carrier. Run slot_siblings@1 after insert_element@1 and "
                "before rule_set@1 and derive_labels@1.",
                {"columns": sorted(set(stale)), "next": NEXT_VALIDATE},
            )
        for clash in (PAIR, VARIANT):
            if clash in names:
                raise StepFailed(
                    "output_column_exists",
                    f"slot_siblings@1 writes {clash!r}, which the input already has.",
                    {"columns": [clash]},
                )
        kinds = set(params.get("kinds") or [])
        for kind in kinds:
            if kind not in vocabulary.values("kind"):
                raise StepFailed(
                    "vocabulary_value_undeclared",
                    f"kinds lists {kind!r}, which the vocabulary does not declare.",
                    {"field": "kinds", "value": kind, "next": NEXT_VALIDATE},
                )
        rows = batch.to_pylist()
        pair_ids: list[str] = []
        added: list[dict[str, Any]] = []
        events = []
        made: Counter[str] = Counter()
        skipped: Counter[str] = Counter()
        carriers = 0
        for row in rows:
            key, occurrence = str(row["_dw_row_key"]), int(row["_dw_occurrence"])
            pair = str(row.get("carrier_id") or key) if "carrier_id" in names else key
            pair_ids.append(pair)
            kind = row.get("kind")
            if (
                row.get("boundary_kind") != SLOT
                or row.get("element_present") is not True
                or kind in vocabulary.decoys
                or (kinds and kind not in kinds)
            ):
                continue
            text = row.get(text_column)
            start, end = row.get("ins_start"), row.get("ins_end")
            if text is None or start is None or end is None:
                continue
            text, start, end = str(text), int(start), int(end)
            carriers += 1
            for variant, found in resolved:
                label = variant_label(variant)
                fills: dict[str, Any] = {
                    "decoy_id": None,
                    "element_rendering": None,
                    "element_normaliser": None,
                }
                rnd = row_random(ctx.step_seed, f"{NAME}:{label}", key, occurrence)
                rendering = None
                if variant["kind"] == "empty":
                    raw = ""
                elif variant["kind"] == "decoy":
                    items = found.generator.items
                    raw = items[rnd.randrange(len(items))]
                    fills.update(kind=variant["ref"], decoy_id=found.id)
                    rendering = by_slug.get(str(row.get("form")))
                elif variant["kind"] == "vetoed":
                    raw = row.get(str(variant["column"]))
                    rendering = by_slug.get(str(row.get("form")))
                    fills.update(kind=variant["as_kind"])
                else:
                    if row.get("form") == variant["ref"]:
                        skipped[f"{label}: the carrier already has this form"] += 1
                        continue
                    raw = row.get(str(element_column))
                    rendering = found
                    fills.update(form=variant["ref"])
                if raw is None:
                    column = variant.get("column") or element_column
                    skipped[f"{label}: no value in {column}"] += 1
                    continue
                # the element as insert_element@1 would have put it: rendered by the form
                value, recovered = _rendered(rendering, str(raw))
                present = bool(value)
                if rendering is not None:
                    fills.update(
                        element_rendering=rendering.rendering,
                        element_normaliser=_normaliser_text(rendering),
                    )
                if value == text[start:end]:
                    skipped[f"{label}: the same text as the carrier"] += 1
                    continue
                new_text = text[:start] + value + text[end:]
                sibling = dict(row)
                sibling[text_column] = new_text
                sibling.update(
                    {
                        "ins_end": start + len(value),
                        "element_inserted": value,
                        "element_present": present,
                        "element_recovered": recovered and present,
                        **{k: v for k, v in fills.items() if k in names},
                    }
                )
                if "length_band" in names:
                    sibling["length_band"] = _band(vocabulary, len(new_text))
                sibling["_dw_origin"] = "generated"
                sibling["_dw_parent_keys"] = [key]
                new_key = ctx.row_key(sibling)
                event = ctx.add(new_key, [key], "slot_sibling", f"code-made sibling ({label})")
                sibling["_dw_row_key"], sibling["_dw_occurrence"] = new_key, event.occurrence
                sibling[PAIR], sibling[VARIANT] = pair, label
                events.append(event)
                added.append(sibling)
                made[label] += 1
        output = batch.append_column(PAIR, pa.array(pair_ids, pa.string())).append_column(
            VARIANT, pa.nulls(batch.num_rows, pa.string())
        )
        schema = output.schema
        if PARENTS not in schema.names:
            schema = schema.append(pa.field(PARENTS, pa.list_(pa.string())))
        added_table = pa.Table.from_pylist(added, schema=schema) if added else None
        return OperatorResult(
            output=output,
            events=events,
            added=added_table,
            output_roles={PAIR: "metadata", VARIANT: "metadata"},
            report={
                "slot_siblings": {
                    "carriers": carriers,
                    "siblings": dict(sorted(made.items())),
                    "skipped": dict(sorted(skipped.items())),
                    "rule_set": (
                        None
                        if document is None
                        else f"{document.name}@{params.get('rule_set_version')}"
                    ),
                }
            },
        )
