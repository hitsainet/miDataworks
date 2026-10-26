"""``slot_siblings@1``: counterfactuals made by code (017 FTASKS 8.1; FR-017.34; FTID §3.6, X-40).

The production chain through 003's real executor, on BOTH fixture behaviours:
``insert_element@1`` -> ``slot_siblings@1`` -> ``rule_set@1`` -> ``derive_labels@1``. Each sibling
is compared with its carrier in UTF-8 bytes outside the element span (the fixture's frames hold
CJK, an emoji and a combining accent around the slot), and its label is the one the declared rules
give it, written here by hand (never copied from the carrier). Controls: the registration removal
(4.3's test), the span (outside bytes changed), ``pair_id`` (not the carrier's) and the origin.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.errors import StepFailed
from src.operators.registry import OperatorRegistry
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator
from tests.support.label_fixtures import (
    alpha_carriers,
    beta_carriers,
    insert_params,
    label_params,
    pin,
    roles_after,
    rule_set_params,
    vocabulary,
)

ALPHA, BETA = "fixture-alpha", "fixture-beta"
MASKED = "XXXX XXXX XXXX 1111"
VARIANTS = [
    {"kind": "empty"},
    {"kind": "decoy", "ref": "lookalike"},
    {"kind": "vetoed", "ref": "masked_value", "column": "masked_value", "as_kind": "masked"},
    {"kind": "rendering", "ref": "spelled"},
]
#: by hand: (kind, framing) -> the label of a row HOLDING an element (fixture-alpha's rule:
#: disclosure in any form unless quoted; invalid and decoy kinds never disclose).
DISCLOSES = {
    ("valid", "direct"): True,
    ("valid", "hedged"): True,
    ("valid", "quoted"): False,
    ("invalid", "direct"): False,
    ("invalid", "hedged"): False,
    ("invalid", "quoted"): False,
    ("masked", "direct"): True,
    ("masked", "hedged"): True,
    ("masked", "quoted"): False,
    ("lookalike", "direct"): False,
    ("lookalike", "hedged"): False,
    ("lookalike", "quoted"): False,
}


def carriers() -> pa.Table:
    rows = [
        {k: v for k, v in r.items() if not k.startswith("_dw_")}
        for r in alpha_carriers().to_pylist()
    ]
    for row in rows:
        row["masked_value"] = MASKED
    return hp.with_system_columns(rows, source_id="src-alpha", content=("text",))


def roles(table: pa.Table) -> dict[str, str]:
    return {
        c: ("content" if c == "text" else "metadata")
        for c in table.schema.names
        if not c.startswith("_dw_")
    }


def sibling_params(key: str = ALPHA, **over: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "variants": json.dumps(VARIANTS),
        "vocabulary": vocabulary(key),
        "element_column": "value",
        **pin(key),
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not None}


def run_siblings(table: pa.Table | None = None, **over: Any) -> tuple[Any, Any]:
    table = table if table is not None else carriers()
    inserted = run_operator("insert_element", insert_params(ALPHA), table, roles(table))
    siblings = run_operator(
        "slot_siblings", sibling_params(**over), inserted.output, roles_after(inserted)
    )
    return inserted, siblings


def labelled(siblings: Any, key: str = ALPHA) -> pa.Table:
    facts = run_operator("rule_set", rule_set_params(key), siblings.output, roles_after(siblings))
    return run_operator("derive_labels", label_params(key), facts.output, roles_after(facts)).output


def test_slot_siblings_is_live_as_a_generator_on_the_curation_queue() -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    assert reg.current_version("slot_siblings") == "1.0.0"
    entry = reg.entry("slot_siblings", "1.0.0")
    assert entry.manifest is not None, entry.error
    assert (entry.manifest.kind, entry.manifest.resources.queue) == ("generator", "curation")
    assert [c.name for c in entry.manifest.output_columns] == ["pair_id", "sibling_variant"]


def test_every_sibling_differs_from_its_carrier_only_inside_the_slot_span() -> None:
    inserted, siblings = run_siblings()
    carriers_by_key = {r["_dw_row_key"]: r for r in inserted.output.to_pylist()}
    rows = siblings.output.to_pylist()
    added = [r for r in rows if r["sibling_variant"] is not None]
    # 54 carriers hold a non-decoy element; spelled carriers get no spelled sibling, and no
    # sibling repeats its carrier's text (a masked carrier's masked value, an invalid carrier's
    # value drawn as the decoy): each is counted, never made
    (report,) = siblings.meta["reports"]
    skipped = report["slot_siblings"]["skipped"]
    same_decoy = skipped.get("decoy:lookalike: the same text as the carrier", 0)
    assert 0 < same_decoy < 18
    assert skipped["vetoed:masked_value: the same text as the carrier"] == 18
    assert skipped["rendering:spelled: the carrier already has this form"] == 27
    assert Counter(r["sibling_variant"] for r in added) == {
        "empty": 54,
        "decoy:lookalike": 54 - same_decoy,
        "vetoed:masked_value": 36,
        "rendering:spelled": 27,
    }
    assert report["slot_siblings"]["siblings"] == dict(Counter(r["sibling_variant"] for r in added))
    for sib in added:
        (parent,) = sib["_dw_parent_keys"] or [None]
        carrier = carriers_by_key[parent]
        start, end = carrier["ins_start"], carrier["ins_end"]
        text, new = carrier["text"], sib["text"]
        assert sib["ins_start"] == start
        # byte-identical outside the span (UTF-8: the frames are multi-byte around the slot)
        assert new[:start].encode() == text[:start].encode()
        assert new[sib["ins_end"] :].encode() == text[end:].encode()
        assert new[start : sib["ins_end"]] == sib["element_inserted"]
        assert sib["element_inserted"] != carrier["element_inserted"]
        assert sib["pair_id"] == (carrier.get("carrier_id") or carrier["_dw_row_key"])
        assert sib["_dw_origin"] == "generated"
        assert sib["_dw_row_key"] != carrier["_dw_row_key"]
    carriers_out = [r for r in rows if r["sibling_variant"] is None]
    assert len(carriers_out) == inserted.output.num_rows
    for row in carriers_out:  # the carriers themselves are unchanged and carry their pair id
        assert row["_dw_origin"] == "source"
        assert row["pair_id"] == (row.get("carrier_id") or row["_dw_row_key"])
    # every sibling shares its carrier's pair id, and pair ids are one per carrier
    pairs = {r["pair_id"] for r in carriers_out}
    assert len(pairs) == len(carriers_out)
    assert {r["pair_id"] for r in added} <= pairs


def test_siblings_are_labelled_by_the_declared_rules_never_copied() -> None:
    _, siblings = run_siblings()
    out = labelled(siblings).to_pylist()
    seen = Counter()
    for row in out:
        variant = row["sibling_variant"]
        kind, framing = row["kind"], row["framing"]
        if variant == "empty":
            assert (row["element_present"], row["discloses"], row["deployed_truth"]) == (
                False,
                False,
                False,
            ), row
        elif variant == "decoy:lookalike":
            assert kind == "lookalike" and row["decoy_id"] == "lookalike"
            assert row["discloses"] is False
            # as insert_element@1 would have put a decoy into a carrier of this form
            assert row["element_rendering"] == ("spelled" if row["form"] == "spelled" else None)
            assert any(c.isdigit() for c in row["element_inserted"]) is (row["form"] == "plain")
        elif variant == "vetoed:masked_value":
            assert kind == "masked"
            assert row["discloses"] is DISCLOSES[("masked", framing)]
            # the veto applies in deployed truth only; a plain masked value is vetoed (a spelled
            # one is invisible to the rule set's signals, so nothing vetoes it)
            assert row["r_vetoed"] is (row["form"] == "plain")
            assert row["deployed_truth"] is (row["discloses"] and not row["r_vetoed"])
        elif variant == "rendering:spelled":
            assert row["form"] == "spelled" and row["element_rendering"] == "spelled"
            assert row["element_recovered"] is True
            assert row["discloses"] is DISCLOSES[(kind, framing)]
        seen[variant] += 1
    # a positive carrier's empty sibling is a negative: the counterfactual flips the label
    flips = [
        r for r in out if r["sibling_variant"] == "empty" and DISCLOSES[(r["kind"], r["framing"])]
    ]
    assert len(flips) == 24 and all(r["discloses"] is False for r in flips)
    assert seen["empty"] == 54


def test_no_sibling_without_an_element_in_a_slot() -> None:
    """fixture-beta's carriers are written whole (no slot): code makes no sibling of them; their
    counterfactuals go through the minimal-pair chain (FR-017.35, 8.3)."""
    table = beta_carriers()
    inserted = run_operator(
        "insert_element", insert_params(BETA, element_column="value"), table, roles(table)
    )
    siblings = run_operator(
        "slot_siblings",
        sibling_params(BETA, variants=json.dumps([{"kind": "empty"}]), element_column=None),
        inserted.output,
        roles_after(inserted),
    )
    assert siblings.output.num_rows == table.num_rows
    assert siblings.output.column("sibling_variant").null_count == table.num_rows
    (report,) = siblings.meta["reports"]
    assert report["slot_siblings"]["carriers"] == 0


def test_kinds_limits_which_carriers_get_siblings() -> None:
    _, siblings = run_siblings(kinds=["valid"], variants=json.dumps([{"kind": "empty"}]))
    added = [r for r in siblings.output.to_pylist() if r["sibling_variant"] is not None]
    assert len(added) == 18 and {r["kind"] for r in added} == {"valid"}


def test_a_step_after_the_labels_is_refused() -> None:
    """A sibling would copy its carrier's label or facts: the step must run before them."""
    inserted, _ = run_siblings()
    facts = run_operator("rule_set", rule_set_params(ALPHA), inserted.output, roles_after(inserted))
    with pytest.raises(StepFailed) as raised:
        run_operator("slot_siblings", sibling_params(), facts.output, roles_after(facts))
    assert raised.value.code == "siblings_after_labels"
    assert "r_fa_hit" in raised.value.details["columns"]


def test_a_variant_the_rule_set_does_not_declare_is_refused() -> None:
    with pytest.raises(StepFailed) as raised:
        run_siblings(variants=json.dumps([{"kind": "rendering", "ref": "spelled"},
                                          {"kind": "vetoed", "ref": "nope", "column": "masked_value",
                                           "as_kind": "masked"}]))  # fmt: skip
    assert raised.value.code == "sibling_variant_undeclared"
    assert raised.value.details["next"]["tool"] == "dataworks_get_rule_set"


@pytest.mark.parametrize(
    ("variants", "fragment"),
    [
        ("[]", "non-empty"),
        ('[{"kind": "swap"}]', "kind is one of"),
        ('[{"kind": "decoy"}]', "needs 'ref'"),
        ('[{"kind": "empty", "ref": "x"}]', "takes no ref"),
        ('[{"kind": "empty"}, {"kind": "empty"}]', "repeats"),
        ('[{"kind": "decoy", "ref": "valid"}]', "not a declared decoy"),
    ],
)
def test_check_params_refuses_bad_variants(variants: str, fragment: str) -> None:
    from src.operators.native.behaviour.slot_siblings import check_sibling_params

    problems = check_sibling_params(sibling_params(variants=variants))
    assert problems and fragment in problems[0], problems


def test_check_params_needs_a_pin_for_a_rule_set_variant() -> None:
    from src.operators.native.behaviour.slot_siblings import check_sibling_params

    unpinned = {
        "variants": json.dumps([{"kind": "decoy", "ref": "lookalike"}]),
        "vocabulary": vocabulary(ALPHA),
    }
    assert "pinned rule set" in check_sibling_params(unpinned)[0]
    empty_only = {**unpinned, "variants": json.dumps([{"kind": "empty"}])}
    assert check_sibling_params(empty_only) == []


def test_the_operator_preview_runs_siblings_through_the_same_checks(data_dir: Path) -> None:
    """The preview (``operators/preview.finish``) applies the step's kind check with the
    manifest's declared columns: a sample previews its siblings as added rows."""
    from src.operators import preview
    from tests.support import operator_fixtures as fx

    inserted, _ = run_siblings()
    fx.write_parts(data_dir / "carriers", inserted.output, parts=2)
    files = sorted((data_dir / "carriers").glob("part-*.parquet"))
    answer = preview.run_preview(
        OperatorRegistry.build(catalogues=(), entry_points=()),
        {
            "operator": "slot_siblings",
            "version": "1.0.0",
            "params": sibling_params(variants=json.dumps([{"kind": "empty"}])),
            "input": {"version_id": "v"},
            "sample_size": 20,
            "seed": 3,
            "mode": "preview",
            "resolved_input": {
                "files": [str(p.relative_to(data_dir)) for p in files],
                "column_roles": roles_after(inserted),
                "rowkey_scheme": "dw.rowkey/v1",
            },
        },
    )
    assert answer["empty"] is False
    assert answer["counts"]["added"] > 0, answer["counts"]


def test_an_unprefixed_rule_output_already_in_the_input_is_refused() -> None:
    """Review R4: a rule set run with no output prefix writes its columns under their own names
    (``vetoed`` among them); a sibling copying one would copy its carrier's fact."""
    inserted, _ = run_siblings()
    table = inserted.output.append_column(
        "vetoed", pa.array([False] * inserted.output.num_rows, pa.bool_())
    )
    with pytest.raises(StepFailed) as raised:
        run_operator("slot_siblings", sibling_params(), table, roles(table))
    assert raised.value.code == "siblings_after_labels"
    assert raised.value.details["columns"] == ["vetoed"]
