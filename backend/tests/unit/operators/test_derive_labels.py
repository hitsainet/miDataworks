"""``derive_labels@1`` over the production chain (017 FTASKS 6.3 – 6.9; FR-017.24 – FR-017.29).

``insert_element@1`` -> ``rule_set@1`` -> ``derive_labels@1`` through 003's real executor, on BOTH
fixture behaviours. The expected labels below are written by hand, one line per combination, with
no wildcard: they do not share the fixture's truth tables (which use wildcards and first-match
order), so a rule and its expectation cannot agree by construction. Controls: M5 (scan input
disabled), M6 (``false`` instead of NULL), M7 (veto dropped), M8 (one per ``sq_*`` table), M9
(vetoed kinds mapped to negative), and the registration removal (4.3's test).
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pyarrow as pa
import pytest

from src.core.config import get_settings
from src.operators.errors import StepFailed
from src.services.behaviour.truth_table import parse_truth_tables
from src.services.behaviour.vocabulary import VocabularyError, parse_vocabulary
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import registry, run_operator
from tests.support.label_fixtures import (
    SLOT,
    alpha_carriers,
    beta_carriers,
    chain,
    insert_params,
    label_params,
    labels,
    pin,
    roles_after,
    rule_set_document,
    rule_set_params,
    vocabulary,
)

ALPHA, BETA = "fixture-alpha", "fixture-beta"

#: fixture-alpha, by hand: (kind, framing) -> discloses. Every form, every placement alike
#: (disclosure in any form, D1); a vetoed kind framed positively IS a disclosure (FR-017.25).
ALPHA_LABEL = {
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
#: fixture-alpha's sub-question, by hand: kind -> sq_element_valid (every element normalises back).
ALPHA_SQ = {
    "valid": "valid_yes",
    "invalid": "valid_no",
    "masked": "valid_no",
    "lookalike": "valid_no",
}
#: fixture-beta, by hand: (framing, planned_complied) -> complies.
BETA_LABEL = {
    ("asks", "complied_yes"): True,
    ("asks", "complied_no"): False,
    ("refuses", "complied_yes"): False,
    ("refuses", "complied_no"): False,
    ("describes", "complied_yes"): True,
    ("describes", "complied_no"): False,
    ("deflects", "complied_yes"): False,
    ("deflects", "complied_no"): False,
}
#: fixture-beta, by hand: (framing, planned_warned) -> sq_warned.
BETA_WARNED = {
    (f, w): ("warned_true" if f == "refuses" else "warned_unclear" if w == "warned_unclear"
             else "warned_false")  # fmt: skip
    for f in ("asks", "refuses", "describes", "deflects")
    for w in ("warned_true", "warned_false", "warned_unclear")
}


def _alpha(**label_over: Any) -> list[dict[str, Any]]:
    params = label_params(ALPHA, **label_over) if label_over else None
    _, _, labelled = chain(ALPHA, alpha_carriers(), label=params)
    return list(labelled.output.to_pylist())


def _beta(contradict: frozenset[str] = frozenset(), **label_over: Any) -> list[dict[str, Any]]:
    _, _, labelled = chain(
        BETA,
        beta_carriers(contradict=contradict),
        insert=insert_params(BETA),
        label=label_params(BETA, **label_over),
    )
    return list(labelled.output.to_pylist())


# --- the label (FR-017.25) -----------------------------------------------------------------------


def test_the_label_matches_the_hand_labels_on_every_combination_of_both_fixtures() -> None:
    alpha = _alpha()
    assert len(alpha) == 72
    covered = Counter((r["kind"], r["framing"], r["form"]) for r in alpha)
    assert len(covered) == 4 * 3 * 2  # every declared kind x framing x form
    for row in alpha:
        assert row["discloses"] is ALPHA_LABEL[(row["kind"], row["framing"])], row["kind"]
    beta = _beta()
    assert len(beta) == 24
    for row in beta:
        assert row["complies"] is BETA_LABEL[(row["framing"], row["planned_complied"])]


def test_a_vetoed_kind_framed_positively_is_labelled_true() -> None:
    """M9: mapping vetoed kinds to negative in the label turns this red."""
    vetoed = [r for r in _alpha() if r["r_vetoed"]]
    assert vetoed and {r["kind"] for r in vetoed} == {"masked"}
    positive = [r for r in vetoed if r["framing"] in ("direct", "hedged")]
    assert positive and all(r["discloses"] is True for r in positive)


@pytest.mark.parametrize("key", [ALPHA, BETA])
def test_changing_any_declared_rule_turns_the_hand_labels_red(key: str) -> None:
    """Every row of the label table decides at least one fixture row: flip any one of them and
    some row no longer matches the hand labels (the fixture exercises every rule)."""
    carriers = alpha_carriers() if key == ALPHA else beta_carriers()
    if key == ALPHA:  # one carrier whose element is missing: nothing is inserted
        empty = {k: v for k, v in carriers.to_pylist()[0].items() if not k.startswith("_dw_")}
        rows = [{k: v for k, v in r.items() if not k.startswith("_dw_")}
                for r in carriers.to_pylist()]  # fmt: skip
        carriers = hp.with_system_columns(
            [*rows, {**empty, "value": None, "text": empty["text"] + " (empty)"}],
            source_id="src-alpha",
            content=("text",),
        )
    inserted = run_operator("insert_element", insert_params(key), carriers,
                            {"text": "content"} | {c: "metadata" for c in carriers.schema.names
                                                   if c != "text" and not c.startswith("_dw_")})  # fmt: skip
    facts = run_operator("rule_set", rule_set_params(key), inserted.output, roles_after(inserted))
    tables = labels(key)["truth_tables"]
    column = labels(key)["label_column"]

    def hand(r: dict[str, Any]) -> bool:
        if key == ALPHA:
            return r["element_present"] and ALPHA_LABEL[(r["kind"], r["framing"])]
        return BETA_LABEL[(r["framing"], r["planned_complied"])]

    for index in range(len(tables["label"]["rows"])):
        changed = json.loads(json.dumps(tables))
        changed["label"]["rows"][index]["value"] = not changed["label"]["rows"][index]["value"]
        out = run_operator(
            "derive_labels",
            label_params(key, truth_tables=json.dumps(changed)),
            facts.output,
            roles_after(facts),
        ).output.to_pylist()
        assert any(r[column] is not hand(r) for r in out), (key, index)


# --- the deployed truth (FR-017.28) --------------------------------------------------------------


def test_deployed_truth_differs_from_the_label_exactly_on_vetoed_positives() -> None:
    """M7: dropping the veto from deployed_truth turns this red."""
    rows = _alpha()
    differ = {r["_dw_row_key"] for r in rows if r["deployed_truth"] != r["discloses"]}
    vetoed_positive = {r["_dw_row_key"] for r in rows if r["r_vetoed"] and r["discloses"]}
    assert differ == vetoed_positive and differ
    assert any(r["r_vetoed"] and not r["discloses"] for r in rows)  # a vetoed negative stays put
    assert all(r["deployed_truth"] is False for r in rows if r["r_vetoed"])


def test_without_a_veto_the_deployed_truth_is_the_label_and_the_report_says_so() -> None:
    reports: list[dict[str, Any]] = []
    _, _, labelled = chain(BETA, beta_carriers(), insert=insert_params(BETA), reports=reports)
    rows = labelled.output.to_pylist()
    assert all(r["deployed_truth"] is r["complies"] for r in rows)
    assert reports[-1]["labels"]["veto"] == "none declared"


# --- sub-questions (FR-017.29), one test each (M8) ----------------------------------------------


def test_sq_element_valid() -> None:
    for row in _alpha():
        assert row["sq_element_valid"] == ALPHA_SQ[row["kind"]], row["kind"]


def test_sq_complied() -> None:
    for row in _beta():
        assert row["sq_complied"] == row["planned_complied"]


def test_sq_warned() -> None:
    for row in _beta():
        assert row["sq_warned"] == BETA_WARNED[(row["framing"], row["planned_warned"])]


def test_no_two_label_columns_agree_by_construction() -> None:
    """Every sq_*, the label and deployed_truth differ on some row (the truth tables reach every
    combination)."""
    alpha = _alpha()
    assert any(
        (r["sq_element_valid"] == "valid_yes") != r["discloses"] for r in alpha
    )  # sq vs label
    assert any(r["deployed_truth"] != r["discloses"] for r in alpha)
    assert any((r["sq_element_valid"] == "valid_yes") != r["deployed_truth"] for r in alpha)
    beta = _beta()
    assert any((r["sq_complied"] == "complied_yes") != r["complies"] for r in beta)
    assert any((r["sq_warned"] == "warned_true") != r["complies"] for r in beta)
    assert any((r["sq_warned"] == "warned_true") != (r["sq_complied"] == "complied_yes")
               for r in beta)  # fmt: skip


def test_one_sub_question_over_the_bound_is_refused_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "behaviour_max_subquestions", 1)
    with pytest.raises(VocabularyError) as refused:
        parse_vocabulary(vocabulary(BETA))  # two sub-questions
    assert refused.value.code == "cap_exceeded"
    assert refused.value.details["setting"] == "BEHAVIOUR_MAX_SUBQUESTIONS"
    assert refused.value.details["next"]["why"] == "read BEHAVIOUR_MAX_SUBQUESTIONS"
    assert parse_vocabulary(vocabulary(ALPHA)).sub_questions  # one: at the bound
    monkeypatch.setattr(get_settings(), "behaviour_max_subquestions", 2)
    assert len(parse_vocabulary(vocabulary(BETA)).sub_questions) == 2


# --- the conflict column (FR-017.27) -------------------------------------------------------------


def test_a_clean_build_has_no_conflict_and_is_measured() -> None:
    rows = _alpha()
    assert {r["label_conflict"] for r in rows} == {False}
    assert {r["label_conflict_reason"] for r in rows} == {None}


def _alpha_rows(texts: dict[int, str]) -> Any:
    base = alpha_carriers().to_pylist()
    rows = []
    for i, text in texts.items():
        row = {k: v for k, v in base[i].items() if not k.startswith("_dw_")}
        row["text"] = text
        rows.append(row)
    return hp.with_system_columns(rows, source_id="src-alpha", content=("text",))


def test_an_invented_element_reads_extra_candidate() -> None:
    """M5: with the scan input disabled this reads no conflict."""
    base = alpha_carriers().to_pylist()
    invalid_plain = next(
        i for i, r in enumerate(base) if (r["kind"], r["form"]) == ("invalid", "plain")
    )
    table = _alpha_rows({invalid_plain: f"The template wrote 4111 1111 1111 1111 itself. {SLOT}"})
    _, _, labelled = chain(ALPHA, table)
    (row,) = labelled.output.to_pylist()
    assert (row["label_conflict"], row["label_conflict_reason"]) == (True, "extra_candidate")


def test_a_moved_element_reads_span_mismatch() -> None:
    base = alpha_carriers().to_pylist()
    assert (base[0]["kind"], base[0]["form"]) == ("valid", "plain")
    table = _alpha_rows({0: base[0]["text"]})
    roles = {c: ("content" if c == "text" else "metadata") for c in table.schema.names
             if not c.startswith("_dw_")}  # fmt: skip
    inserted = run_operator("insert_element", insert_params(ALPHA), table, roles).output
    (row,) = inserted.to_pylist()
    element = row["element_inserted"]
    moved = row["text"].replace(element, "") + " moved here: " + element
    inserted = inserted.set_column(
        inserted.schema.get_field_index("text"), "text", pa.array([moved], pa.string())
    )
    facts = run_operator("rule_set", rule_set_params(ALPHA), inserted, roles_after_table(inserted))
    out = run_operator("derive_labels", label_params(ALPHA), facts.output, roles_after(facts))
    (labelled,) = out.output.to_pylist()
    assert (labelled["label_conflict"], labelled["label_conflict_reason"]) == (
        True,
        "span_mismatch",
    )


def roles_after_table(table: Any) -> dict[str, str]:
    return {c: ("content" if c == "text" else "metadata") for c in table.schema.names
            if not c.startswith("_dw_")}  # fmt: skip


def test_an_instructed_carrier_against_its_target_reads_target_contradicted() -> None:
    """FR-017.15 / FR-017.27: a generator that wrote the opposite of its instruction is caught by
    the rule signal (fixture-beta, no element)."""
    rows = _beta(contradict=frozenset({"asks"}))
    by = Counter((r["framing"], r["label_conflict_reason"]) for r in rows)
    assert by[("asks", "target_contradicted")] == 6
    assert all(r["label_conflict"] is False for r in rows if r["framing"] != "asks")


@pytest.mark.parametrize("key", [ALPHA, BETA])
def test_with_no_rule_signal_the_conflict_is_null_never_false(key: str) -> None:
    """M6: writing false instead of NULL turns this red."""
    reports: list[dict[str, Any]] = []
    carriers = alpha_carriers() if key == ALPHA else beta_carriers()
    if key == ALPHA:  # no rule set: no decoys either (a decoy is the rule set's)
        carriers = carriers.filter(pa.compute.not_equal(carriers.column("kind"), "lookalike"))
    _, _, labelled = chain(
        key,
        carriers,
        label=label_params(key, pinned=False, scan_column=None, target_checks="[]"),
        reports=reports,
    )
    rows = labelled.output.to_pylist()
    assert rows and {r["label_conflict"] for r in rows} == {None}
    assert {r["label_conflict_reason"] for r in rows} == {None}
    assert reports[-1]["labels"]["conflict"] == "not measured"


# --- decoys (FR-017.24) --------------------------------------------------------------------------


def test_a_decoy_a_positive_signal_fires_on_is_excluded_and_counted() -> None:
    document = rule_set_document(ALPHA)
    (decoy,) = document["decoys"]
    leaky = {**document, "decoys": [{**decoy, "generator": {"list": ["4111 1111 1111 1111"]}}]}
    carriers = alpha_carriers()
    roles = roles_after_table(carriers)
    reports: list[dict[str, Any]] = []
    inserted = run_operator(
        "insert_element", insert_params(ALPHA, **pin(ALPHA, leaky)), carriers, roles
    )
    facts = run_operator(
        "rule_set", {**rule_set_params(ALPHA), **pin(ALPHA, leaky)}, inserted.output,
        roles_after(inserted),
    )  # fmt: skip
    out = run_operator(
        "derive_labels", label_params(ALPHA, **pin(ALPHA, leaky)), facts.output,
        roles_after(facts), report_sink=reports,
    )  # fmt: skip
    excluded = out.events_by_reason("decoy_accidental_match")
    plain_lookalikes = [r for r in inserted.output.to_pylist()
                        if r["kind"] == "lookalike" and r["form"] == "plain"]  # fmt: skip
    assert len(excluded) == len(plain_lookalikes) == 9
    assert {e["statistic_name"] for e in excluded} == {"r_fa_hit"}
    assert {e["statistic_text"] for e in excluded} == {"r_fa_hit=True"}  # no row text
    assert reports[-1]["labels"]["decoys_excluded"] == {"lookalike": 9}
    kept = out.output.to_pylist()
    assert len(kept) == 72 - 9
    # the fixture's own decoys never fire: none excluded, each labelled negative
    clean = _alpha()
    lookalikes = [r for r in clean if r["kind"] == "lookalike"]
    assert len(lookalikes) == 18 and not any(r["discloses"] for r in lookalikes)


# --- refusals ------------------------------------------------------------------------------------


def test_a_truth_table_with_a_missing_cell_is_refused_at_validation_and_in_the_worker() -> None:
    tables = labels(ALPHA)["truth_tables"]
    holed = {**tables, "label": {**tables["label"], "rows": tables["label"]["rows"][:-1]}}
    with pytest.raises(VocabularyError) as refused:
        parse_truth_tables(json.dumps(holed), parse_vocabulary(vocabulary(ALPHA)))
    assert refused.value.code == "truth_table_not_total"
    assert refused.value.details["table"] == "label"
    assert refused.value.details["missing"][0]["kind"] in ("invalid", "lookalike")
    problems = registry().validate_params(
        "derive_labels", "1.0.0", label_params(ALPHA, truth_tables=json.dumps(holed))
    )
    assert any("truth_table_not_total" in p for p in problems), problems
    # a recipe validated with "@input" meets the same refusal when the build runs it
    inserted = run_operator("insert_element", insert_params(ALPHA), alpha_carriers(),
                            roles_after_table(alpha_carriers()))  # fmt: skip
    from src.operators.native.behaviour.derive_labels import DeriveLabels

    with pytest.raises(StepFailed) as worker:
        DeriveLabels().run(
            inserted.output, label_params(ALPHA, truth_tables=json.dumps(holed)), None  # type: ignore[arg-type]
        )
    assert worker.value.code == "truth_table_not_total"


def test_every_declared_sub_question_needs_its_table() -> None:
    tables = labels(BETA)["truth_tables"]
    missing = {**tables, "sub_questions": {"complied": tables["sub_questions"]["complied"]}}
    with pytest.raises(VocabularyError) as refused:
        parse_truth_tables(json.dumps(missing), parse_vocabulary(vocabulary(BETA)))
    assert refused.value.details["missing_tables"] == ["warned"]


def test_a_null_fact_is_refused_naming_the_column_never_guessed() -> None:
    from src.services.behaviour.truth_table import TruthTables  # noqa: F401

    tables = parse_truth_tables(
        json.dumps(labels(ALPHA)["truth_tables"]), parse_vocabulary(vocabulary(ALPHA))
    )
    with pytest.raises(VocabularyError) as refused:
        tables.label.evaluate({"kind": "valid", "framing": "direct", "element_present": None})
    assert refused.value.code == "truth_table_input_undeclared"
    assert refused.value.details["input"] == "element_present"


def test_the_scan_needs_the_pinned_rule_set() -> None:
    problems = registry().validate_params(
        "derive_labels", "1.0.0", label_params(ALPHA, pinned=False)
    )
    assert any("scan_column" in p for p in problems), problems


# --- Review R3 --------------------------------------------------------------------------------


def _hand_rows(rows: list[dict[str, Any]]) -> Any:
    """Rows written by hand AFTER the rule set (no insert/scan run): the conflict rule alone."""
    filled = [
        {
            "text": "t",
            "kind": "valid",
            "framing": "direct",
            "form": "plain",
            "element_present": True,
            "element_recovered": True,
            "r_vetoed": False,
            "r_fa_hit": True,
            "decoy_id": None,
            "planned_element_valid": "valid_yes",
            **row,
        }
        for row in rows
    ]
    return hp.with_system_columns(filled, source_id="src-hand", content=("text",))


def _label(table: Any, **over: Any) -> Any:
    return run_operator(
        "derive_labels", label_params(ALPHA, **over), table, roles_after_table(table)
    )


def test_a_carrier_with_no_element_on_which_a_signal_fires_reads_extra_candidate() -> None:
    """R3 survivor: a row with NO planned element (placement none) whose text holds a valid
    value the generator invented is the plainest conflict; the fixture had no such row."""
    base = alpha_carriers().to_pylist()[0]
    rows = [
        {**{k: v for k, v in base.items() if not k.startswith("_dw_")},
         "text": text, "slot": None, "kind": "invalid", "value": None}
        for text in ("Nothing planned, yet 4111 1111 1111 1111 appears.", "Nothing at all here.")
    ]  # fmt: skip
    table = hp.with_system_columns(rows, source_id="src-alpha", content=("text",))
    _, _, labelled = chain(ALPHA, table)
    invented, clean = labelled.output.to_pylist()
    assert invented["element_present"] is False and invented["r_fa_hit"] is True
    assert (invented["label_conflict"], invented["label_conflict_reason"]) == (
        True, "extra_candidate",
    )  # fmt: skip
    assert (clean["label_conflict"], clean["label_conflict_reason"]) == (False, None)


@pytest.mark.parametrize(
    ("spans", "reason"),
    [
        ("[[10, 29]]", None),  # exactly where planned
        ("[[29, 48]]", "span_mismatch"),  # TOUCHING the planned span is not overlapping it
        ("[[0, 10]]", "span_mismatch"),
        ("[[12, 20]]", None),  # inside the planned span
        ("[[10, 29], [40, 59]]", "extra_candidate"),
    ],
)
def test_the_span_rule_on_hand_written_candidates(spans: str, reason: str | None) -> None:
    table = _hand_rows(
        [{"ins_start": 10, "ins_end": 29, "element_inserted": "4111 1111 1111 1111",
          "r_fa_spans": spans}]  # fmt: skip
    )
    (row,) = _label(table).output.to_pylist()
    assert (row["label_conflict"], row["label_conflict_reason"]) == (reason is not None, reason)


def test_the_report_counts_the_vetoed_rows() -> None:
    reports: list[dict[str, Any]] = []
    _, _, labelled = chain(ALPHA, alpha_carriers(), reports=reports)
    vetoed = sum(1 for r in labelled.output.to_pylist() if r["r_vetoed"])
    assert vetoed > 0
    assert reports[-1]["labels"]["veto"] == {"column": "r_vetoed", "rows_vetoed": vetoed}


def _leaky_chain(**label_over: Any) -> Any:
    document = rule_set_document(ALPHA)
    (decoy,) = document["decoys"]
    leaky = {**document, "decoys": [{**decoy, "generator": {"list": ["4111 1111 1111 1111"]}}]}
    carriers = alpha_carriers()
    inserted = run_operator(
        "insert_element", insert_params(ALPHA, **pin(ALPHA, leaky)), carriers,
        roles_after_table(carriers),
    )  # fmt: skip
    facts = run_operator(
        "rule_set", {**rule_set_params(ALPHA), **pin(ALPHA, leaky)}, inserted.output,
        roles_after(inserted),
    )  # fmt: skip
    return inserted, facts, {**label_params(ALPHA, **pin(ALPHA, leaky)), **label_over}


def test_a_decoy_check_never_passes_by_absence() -> None:
    """R3 F3: with the must_not_fire column absent (another output_prefix, or no rule_set@1 step
    at all) every leaky decoy was KEPT and labelled negative while its text held a valid value.
    Refused instead; a null flag is refused too."""
    inserted, facts, params = _leaky_chain()
    other_prefix = {k: v for k, v in params.items() if k != "scan_column"}
    other_prefix["output_prefix"] = "zz_"
    with pytest.raises(StepFailed) as absent:
        run_operator("derive_labels", other_prefix, facts.output, roles_after(facts))
    assert absent.value.code == "decoy_check_missing"
    assert absent.value.details["columns"] == ["zz_fa_hit"]
    no_rule_step = {k: v for k, v in params.items()
                    if k not in ("scan_column", "vetoed_column")}  # fmt: skip
    with pytest.raises(StepFailed) as skipped:
        run_operator("derive_labels", no_rule_step, inserted.output, roles_after(inserted))
    assert skipped.value.code == "decoy_check_missing"
    table = facts.output
    index = table.schema.get_field_index("r_fa_hit")
    blank = table.set_column(index, "r_fa_hit", pa.array([None] * table.num_rows, pa.bool_()))
    with pytest.raises(StepFailed) as null:
        run_operator("derive_labels", params, blank, roles_after(facts))
    assert null.value.code == "fact_not_boolean"


def test_a_shadowed_truth_table_row_is_refused_and_a_redundant_one_is_kept() -> None:
    """R3 F2: first-match let a wildcard row silently shadow a later, more specific row with a
    different answer (``valid`` x ``quoted`` labelled positive against the author's rule)."""
    v = parse_vocabulary(vocabulary(ALPHA))
    sq = labels(ALPHA)["truth_tables"]["sub_questions"]
    shadowed = {"label": {"inputs": ["kind", "framing"], "rows": [
        {"when": {"kind": "valid"}, "value": True},
        {"when": {"kind": "valid", "framing": "quoted"}, "value": False},
        {"when": {}, "value": False}]}, "sub_questions": sq}  # fmt: skip
    with pytest.raises(VocabularyError) as refused:
        parse_truth_tables(shadowed, v)
    assert refused.value.code == "truth_table_row_shadowed"
    assert (refused.value.details["row"], refused.value.details["shadowed_by"]) == (1, 0)
    assert refused.value.details["combination"] == {"kind": "valid", "framing": "quoted"}
    problems = registry().validate_params(
        "derive_labels", "1.0.0", label_params(ALPHA, truth_tables=json.dumps(shadowed))
    )
    assert any("truth_table_row_shadowed" in p for p in problems), problems
    redundant = {"label": {"inputs": ["kind"], "rows": [
        {"when": {}, "value": False},
        {"when": {"kind": "invalid"}, "value": False}]}, "sub_questions": sq}  # fmt: skip
    assert parse_truth_tables(redundant, v).label.evaluate({"kind": "invalid"}) is False
