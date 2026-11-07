"""The pack's end-to-end pipeline (ENH-006 D110): what a rerun needs, and what the dry run learned.

The first SSN dry run found the pack could not reproduce its own run: the templates, sizes and
held-out corpora had moved on in live recipes while the files stayed at their first draft, and the
two-pass build existed nowhere in the repository. Each test here fails if a lesson is lost: read
through the live operator registry and the operators' own rule parser, not by matching text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from src.core.canonical_json import canonical_sha256
from src.operators.native.identifiers.text_ops import _holds, check_pattern, parse_rule
from src.operators.registry import OperatorRegistry
from src.rules import dialect
from src.services.behaviour.truth_table import parse_truth_tables
from src.services.behaviour.vocabulary import parse_vocabulary
from tests.support.source_tree import REPO

PACK = REPO / "behaviours" / "dlp-ssn-us"
RUN_IDENTIFIER = re.compile(r"\b(gr|lr|rq|dts)_[0-9a-f]{8,}\b|\b[0-9A-F]{8}\b")
FILLED = {
    "generation_run_id": "gr_0123456789abcdef",
    "label_run_id": "lr_0123456789abcdef",
    "slot_hex_alternatives": "46DE0D0F|52637EF7",
}


def load(name: str) -> Any:
    return json.loads((PACK / name).read_text(encoding="utf-8"))


def stage(stage_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in load("pipeline.json")["stages"] if s["id"] == stage_id)
    return found


def steps(stage_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = stage(stage_id)["recipe"]["steps"]
    return found


def step_named(stage_id: str, operator: str, **match: Any) -> dict[str, Any]:
    for step in steps(stage_id):
        if step["operator"] == operator and all(
            step["params"].get(k) == v for k, v in match.items()
        ):
            return step
    raise AssertionError(f"{stage_id} has no {operator} {match}")


def recipes() -> Iterator[tuple[str, dict[str, Any]]]:
    for item in load("pipeline.json")["stages"]:
        if "recipe" in item:
            yield item["id"], item["recipe"]


def filled(value: Any) -> Any:
    text = json.dumps(value)
    for name, sample in FILLED.items():
        text = text.replace("{{" + name + "}}", sample)
    return json.loads(text)


# --- the pipeline is whole and every step is a real one ------------------------------------------


def test_the_stages_run_in_the_order_the_dry_run_settled_on() -> None:
    order = [s["id"] for s in load("pipeline.json")["stages"]]
    must_follow = [
        ("templates", "generation"),
        ("plans", "role_column"),
        ("role_column", "generation"),
        ("generation", "pass1"),
        ("pass1", "merged_pass1"),
        ("merged_pass1", "second_opinion"),
        ("second_opinion", "calibration_labels"),
        ("second_opinion", "pass2"),
        ("negatives_sources", "negatives"),
        ("pass2", "main_set"),
        ("negatives", "main_set"),
        ("everyday_sources", "everyday_negatives"),
        ("everyday_negatives", "main_set"),
        ("calibration_labels", "send"),
        ("check", "send"),
        ("send", "train"),
    ]
    for first, then in must_follow:
        assert order.index(first) < order.index(then), (first, then)


def test_every_recipe_step_is_a_live_operator_with_valid_parameters() -> None:
    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    checked = 0
    for name, recipe in recipes():
        for step in filled(recipe)["steps"]:
            manifest = registry.entry(step["operator"], step["version"]).manifest
            jsonschema.validate(step["params"], manifest.params_schema)
            checked += 1
        assert recipe["steps"], name
    assert checked > 40, "the walk found almost nothing: this test would check nothing"


def test_every_stage_tool_is_on_the_published_contract() -> None:
    contract = (REPO / "docs" / "mcp-contract.md").read_text(encoding="utf-8")
    tools = [
        s["tool"] for s in load("pipeline.json")["stages"] if s["tool"].startswith("dataworks_")
    ]
    assert len(tools) > 10
    for tool in tools:
        assert f"`{tool}`" in contract or tool in contract, tool


def test_every_placeholder_is_declared_and_no_run_identifier_is_left() -> None:
    pipeline = load("pipeline.json")
    text = json.dumps(pipeline["stages"])
    used = set(re.findall(r"\{\{([a-z0-9_]+)\}\}", text))
    assert used and used <= set(pipeline["placeholders"]), used - set(pipeline["placeholders"])
    assert RUN_IDENTIFIER.findall(text) == [], "a run's own identifier was left in the pipeline"


def test_the_rule_set_pin_is_the_packs_everywhere() -> None:
    pin = load("recipe.json")["rule_set_pin"]
    readers = 0
    for _, recipe in recipes():
        for step in recipe["steps"]:
            if "rule_set_sha256" in step["params"]:
                readers += 1
                assert {k: step["params"].get(k) for k in pin} == pin, step["operator"]
    assert readers >= 4


# --- what the dry run got wrong -------------------------------------------------------------------


VETOED_KINDS = {
    "masked",
    "wrong_length",
    "bad_check",
}  # ENH-006 D124/D125: nothing a reader can glean
DISCLOSING = {"pos", "glued_letters"}  # D124: all nine digits of a valid value read back


def veto_step(source: str) -> tuple[dict[str, Any], dict[str, Any]]:
    body = steps("pass2") if source == "pipeline" else load("recipe.json")["steps"]
    derive = [s["operator"] for s in body].index("derive_labels")
    return body[derive - 1], body[derive]


@pytest.mark.parametrize("source", ["pipeline", "recipe"])
def test_the_veto_covers_every_kind_no_reader_can_glean(source: str) -> None:
    """The rule's veto cannot see a rendered mask (1,798 masks read as disclosures through it), so the
    plan's kind is the veto. D125 widens it from masked to every kind whose value no reader can glean as
    a valid SSN; glued letters still give all nine digits back (D124). Compiled by the operator's own
    `check_pattern`, run on every planned kind."""
    plan, derive = veto_step(source)
    assert plan["operator"] == "regex_match" and plan["params"]["column"] == "kind"
    assert derive["params"]["vetoed_column"] == plan["params"]["output_prefix"] + "matched"
    assert load("labels.json")["vetoed_column"] == derive["params"]["vetoed_column"]
    compiled = check_pattern(plan["params"]["pattern"], bool(plan["params"].get("ignore_case")))
    kinds = load("plan.json")["vocabulary"]["kinds"]
    assert {k for k in kinds if compiled.search(k)} == VETOED_KINDS


@pytest.mark.parametrize("source", ["pipeline", "recipe"])
def test_deployed_truth_is_d124_on_every_kind_and_framing(source: str) -> None:
    """deployed_truth = label AND NOT veto, through each build's own embedded label table and compiled
    veto: a disclosure is a gleanable valid value presented as an SSN, an example included (D124), never
    a masked, wrong-length or impossible one (D125). Both builds carry the pack's table: the pipeline's
    copy was pinned by nothing, and a mutation of it alone left the suite green."""
    plan, derive = veto_step(source)
    assert json.loads(derive["params"]["truth_tables"]) == load("labels.json")["truth_tables"]
    compiled = check_pattern(plan["params"]["pattern"], False)
    vocabulary_json = json.dumps(load("plan.json")["vocabulary"])
    tables = parse_truth_tables(derive["params"]["truth_tables"], parse_vocabulary(vocabulary_json))
    vocabulary = load("plan.json")["vocabulary"]
    wrong = []
    for kind in vocabulary["kinds"]:
        for framing in vocabulary["framings"]:
            row = {"kind": kind, "framing": framing, "element_recovered": True}
            truth = bool(tables.label.evaluate(row)) and not compiled.search(kind)
            expected = kind in DISCLOSING and framing in {
                "own_identifier",
                "someones_identifier",
                "discussed_in_general",
            }
            if truth != expected:
                wrong.append((kind, framing, truth))
    assert wrong == []


def invented_rows_dropped(row: dict[str, Any]) -> bool:
    for rule in step_named("pass2", "rule_label", output_column="e_invented")["params"]["rules"]:
        value, conditions = parse_rule(rule)
        if all(_holds(row.get(c), op, literal) for c, op, literal in conditions):
            return bool(value == "drop")
    return False


@pytest.mark.parametrize(
    ("kind", "truth", "reason", "conflict", "dropped"),
    [
        ("pos", False, "ok", "extra_candidate", True),  # an invented valid number (D104)
        ("lookalike", False, "bad_range", None, True),  # an invented invalid one (D110)
        ("naming_no_value", False, "bad_range", None, True),
        ("masked", False, "illegal_character", None, True),
        ("wrong_length", False, "bad_range", None, True),
        ("bad_check", False, "bad_range", None, False),  # its own planned element, not invented
        ("pos", False, None, None, False),  # a clean hard negative
        ("pos", True, "bad_range", None, False),  # positives keep their label and stay
        ("pos", True, "ok", "extra_candidate", False),
    ],
)
def test_pass2_drops_negatives_holding_an_invented_number(
    kind: str, truth: bool, reason: str | None, conflict: str | None, dropped: bool
) -> None:
    row = {
        "kind": kind,
        "deployed_truth": truth,
        "r_ssn_reason": reason,
        "label_conflict_reason": conflict,
    }
    assert invented_rows_dropped(row) is dropped, row


def framing_contradiction(row: dict[str, Any]) -> bool:
    for rule in step_named("pass2", "rule_label", output_column="e_drop")["params"]["rules"]:
        value, conditions = parse_rule(rule)
        if all(_holds(row.get(c), op, literal) for c, op, literal in conditions):
            return bool(value == "framing")
    return False


@pytest.mark.parametrize(
    ("kind", "framing", "second_opinion", "dropped"),
    [
        ("pos", "own_identifier", "negative", True),  # the plan presents it, the classifier says no
        (
            "pos",
            "discussed_in_general",
            "negative",
            False,
        ),  # D124: an example is no one's, so no is expected
        (
            "pos",
            "discussed_in_general",
            "positive",
            False,
        ),  # D124: dropped before; both answers are a disclosure
        ("glued_letters", "someones_identifier", "negative", True),
        (
            "bad_check",
            "own_identifier",
            "negative",
            True,
        ),  # presentation, not validity: a contradiction
        ("wrong_length", "own_identifier", "negative", True),
        ("masked", "someones_identifier", "negative", True),
        ("lookalike", "own_identifier", "negative", True),
        ("naming_no_value", "own_identifier", "negative", False),  # no value to present
        ("pos", "other_number", "positive", True),  # D115: a writer told to leave it out
        ("pos", "naming_no_value", "positive", True),
        ("pos", "other_type", "positive", False),  # D115: other_type stays out
        ("pos", "own_identifier", "positive", False),
    ],
)
def test_the_second_opinion_drops_only_a_real_contradiction(
    kind: str, framing: str, second_opinion: str, dropped: bool
) -> None:
    """A row is dropped when the classifier's answer to the presented question contradicts the plan's
    framing (D115). The question asks whether a number is presented as a particular person's SSN, so
    it judges framing, not validity: a vetoed kind framed as someone's is still expected yes. A value
    given as an example (discussed_in_general) is a disclosure under D124 whatever the classifier
    says about presentation, so that framing is never checked against it.
    """
    row = {"kind": kind, "framing": framing, "label": second_opinion, "element_recovered": True}
    assert framing_contradiction(row) is dropped, row


def test_the_contradiction_rules_read_the_presented_question() -> None:
    """The e_drop rules are written for sq_presented's answers. A run on another question (the
    disclosure question answers no on every vetoed kind) would turn them into a wrong filter, so a
    change of question must come with a change of rules."""
    stage = next(s for s in load("pipeline.json")["stages"] if s["id"] == "second_opinion")
    assert stage["args"]["question"] == "second_opinion.json questions, the presented question"
    assert "particular person" in load("second_opinion.json")["questions"]["sq_presented"]


def test_the_invented_filter_actually_runs_and_its_helpers_leave() -> None:
    operators = steps("pass2")
    at = operators.index(step_named("pass2", "rule_label", output_column="e_invented"))
    assert operators[at + 1]["operator"] == "metadata_value_filter"
    assert operators[at + 1]["params"] == {"column": "e_invented", "values": ["drop"]}
    assert "e_*" in operators[at + 2]["params"]["patterns"]


@pytest.mark.parametrize(
    "text",
    ["[[SLOT-46DE0D0F]]", "{{SLOT-46DE0D0F}}", "[SLOT-46de0d0f]", "SLOT 46DE0D0F", "slot:46de0d0f"],
)
def test_the_leak_check_catches_a_reshaped_marker(text: str) -> None:
    leak = step_named("pass1", "regex_match", output_prefix="e_leak_")["params"]
    assert re.search(leak["pattern"], text, re.IGNORECASE), text


def test_the_leak_check_leaves_ordinary_slot_words_alone() -> None:
    leak = step_named("pass1", "regex_match", output_prefix="e_leak_")["params"]
    for text in ("a slot machine", "Time slot 12 is free", "the mail slot 46"):
        assert not re.search(leak["pattern"], text, re.IGNORECASE), text


def test_pass2_drops_a_bare_marker_as_a_leak_from_every_run() -> None:
    """A writer that keeps only the hex shows no SLOT word, so each run's hex goes in."""
    marker = step_named("pass2", "regex_match", output_prefix="e_slot_")["params"]
    assert marker["pattern"] == "{{slot_hex_alternatives}}" and marker["ignore_case"] is True
    rules = step_named("pass2", "rule_label", output_column="e_drop")["params"]["rules"]
    assert parse_rule(rules[0]) == ("leak", [("e_slot_matched", "==", True)])
    assert parse_rule(rules[1]) == ("leak", [("e_leak_matched", "==", True)])
    drop = step_named("pass2", "metadata_value_filter", column="e_drop")
    assert drop["params"]["values"] == ["leak", "framing"]


def test_no_plans_index_range_overlaps_another() -> None:
    """Carrier ids follow the index: the top-up inside the Llama range joined 2,659 strangers."""
    ranges = sorted(
        (p["index_start"], p["index_start"] + p["rows"], p["name"])
        for p in load("sizes.json")["plans"]
    )
    assert len(ranges) >= 3
    for (_, end, name), (start, _, other) in zip(ranges, ranges[1:], strict=False):
        assert end <= start, f"{name} overlaps {other}"


def test_the_plans_are_built_with_ids_that_cannot_collide() -> None:
    assert stage("plans")["step"] == "carrier_plan@1.1.0"


def test_the_merge_refuses_shared_carrier_ids_before_the_second_opinion_is_paid_for() -> None:
    """Asserted on the stage, since ``slot_siblings`` and ``split`` refuse too, but only after it."""
    operators = [s["operator"] for s in steps("merged_pass1")]
    assert operators.count("check_carrier_ids") == 1
    order = [s["id"] for s in load("pipeline.json")["stages"]]
    assert order.index("merged_pass1") < order.index("second_opinion")


def test_no_step_repairs_carrier_ids_after_the_fact() -> None:
    columns = {s["params"].get("output_column") for _, r in recipes() for s in r["steps"]}
    assert "e_carrier" not in columns and "carrier_id" not in columns


def test_the_split_is_the_packs_and_has_an_in_distribution_test() -> None:
    declared = load("splits.json")["params"]
    assert step_named("pass2", "split")["params"] == declared
    fractions = dict(zip(declared["split_names"], declared["split_fractions"], strict=True))
    assert fractions["id_test"] > 0
    assert "id_test" not in declared["held_out"]


def test_the_plans_role_column_is_dropped_before_the_audit_reads_it() -> None:
    """It spells out the planned framing, so left in it predicts the label by construction."""
    operators = steps("pass2")
    last_drop = [s for s in operators if s["operator"] == "drop_columns"][-1]
    assert "role" in last_drop["params"]["patterns"]
    assert operators.index(last_drop) < [s["operator"] for s in operators].index("split")


# --- what a rerun reads ---------------------------------------------------------------------------


def test_every_genre_has_the_template_that_ran() -> None:
    templates = load("templates.json")
    assert set(templates["ran"]) == set(templates["template_map"]["templates"])
    assert templates["ran"]["voice_transcript"].endswith("v4@2")
    for genre, name in templates["template_map"]["templates"].items():
        body = templates["templates"][name]["body"]
        assert set(body) >= {"prompt", "system", "sampling"}, genre
        # the body itself, so an edit has to say which run it belongs to
        assert canonical_sha256(body) == templates["ran_sha256"][genre], genre


def test_the_plans_are_the_sizes_that_ran() -> None:
    plans = {p["name"]: p for p in load("sizes.json")["plans"]}
    assert (plans["llama"]["rows"], plans["qwen"]["rows"]) == (21900, 8700)
    for name in ("llama", "qwen"):
        assert plans[name]["templated_share"] == 0.0
        assert abs(sum(plans[name]["genre_shares"].values()) - 1.0) < 1e-6
    assert plans["llama_topup"]["optional"] is True


def test_every_real_text_corpus_is_pinned_and_can_import() -> None:
    corpora = load("benchmarks.json")["real_text"]
    assert {c["language"] for c in corpora} == {"en", "de", "es"}
    for corpus in corpora:
        assert corpus["repo_id"] != "HuggingFaceFW/fineweb", "refused: it ships a loading script"
        assert re.fullmatch(r"[0-9a-f]{40}", corpus["revision"]), corpus["repo_id"]
        assert corpus["data_files"] and corpus["rows"] > 0


def test_negatives_are_sampled_per_language_after_the_screen() -> None:
    body = steps("negatives")
    assert [s["operator"] for s in body] == ["rule_set", "screen_filter", "length_band", "sample"]
    assert body[0]["params"]["use"] == "screen"
    assert set(body[1]["params"]["flag_columns"]) == {"s_ssn_hit", "s_ssn_cued"}
    sample = body[3]["params"]
    assert sample["mode"] == "stratified" and sample["strata"] == ["language"]
    assert sample["per_stratum"] == load("sizes.json")["calibration_negatives"]["per_language"]
    assert sample["per_stratum"] >= 2000  # D87's floor for a bar, in each language


def test_everyday_text_shares_no_file_with_the_calibration_negatives() -> None:
    """ENH-006 D128: the training negatives come from the same corpora as the calibration negatives,
    so they must read other files, or a bar would be cut on rows a probe trained on."""
    bench = load("benchmarks.json")
    calibration = {
        (c["repo_id"], c["config"], f) for c in bench["real_text"] for f in c["data_files"]
    }
    everyday = bench["everyday_text"]
    assert {c["language"] for c in everyday} == {"en", "de", "es"}
    pinned = {(c["repo_id"], c["config"]): c["revision"] for c in bench["real_text"]}
    for corpus in everyday:
        assert re.fullmatch(r"[0-9a-f]{40}", corpus["revision"]), corpus["repo_id"]
        assert pinned[(corpus["repo_id"], corpus["config"])] == corpus["revision"]
        for f in corpus["data_files"]:
            assert (corpus["repo_id"], corpus["config"], f) not in calibration, f
    assert all("openwebtext" not in c["repo_id"].lower() for c in everyday)


def test_everyday_negatives_are_screened_then_kept_only_where_a_number_appears() -> None:
    body = steps("everyday_negatives")
    assert [s["operator"] for s in body] == [
        "rule_set",
        "screen_filter",
        "length_band",
        "regex_match",
        "rule_label",
        "metadata_value_filter",
        "drop_columns",
        "sample",
    ]
    assert body[:3] == steps("negatives")[:3]  # the calibration negatives' screen, unchanged
    pattern = dialect.compile_pattern(body[3]["params"]["pattern"], True)
    for text in ("Order 4471 shipped", "Pi is 3.14 here", "call one two three", "on 1/2/2020"):
        assert pattern.search(text), text
    for text in ("one of the best days", "No numbers at all here", "It costs 12 euros"):
        assert not pattern.search(text), text
    assert body[4]["params"]["rules"] == [
        f"drop if {body[3]['params']['output_prefix']}matched == false"
    ]
    assert body[5]["params"] == {"column": body[4]["params"]["output_column"], "values": ["drop"]}
    sample = body[7]["params"]
    assert sample["strata"] == ["language"]
    assert sample["per_stratum"] == load("sizes.json")["everyday_negatives"]["per_language"] >= 2000


def test_the_audit_skips_only_what_the_label_comes_from() -> None:
    """Skipping a column hides a shortcut in it, and the check's text says the label came from it.
    The dry run once listed these and D-3 could not test them."""
    train = next(r for r in stage("main_set")["args"]["roles"] if r["role"] == "train")
    skipped = set(train["label_source_columns"])
    never = {
        "idx",
        "slot",
        "form",
        "genre",
        "generator",
        "placement",
        "length_band",
        "boundary_kind",
        "generation_record_index",
        "ins_start",
        "ins_end",
        "framing_phrase",
        "element_rendering",
        "element_normaliser",
        "pair_id",
        "carrier_id",
    }
    assert skipped & never == set()
    assert {"kind", "framing", "element_recovered"} <= skipped


def test_the_calibration_queue_samples_before_the_filter_it_judges() -> None:
    args = stage("calibration_labels")["args"]
    assert args["kind"] == "calibration_labeling"
    assert args["version_id"] == "{{merged_version}}"  # not pass 2: that would be circular
    assert args["label_run_id"] == "{{label_run_id}}" and args["size"] >= 200
    assert "show_model_output" not in args  # hidden by default, and it must stay hidden
    assert stage("calibration_labels")["human"] is True


def test_the_human_eval_stage_reads_the_set_by_row_key_and_keeps_masked_separable() -> None:
    """ENH-006 D116 G6: a 13 KB recipe of carrier ids became one step naming the set."""
    first, second = steps("human_eval")
    assert (first["operator"], first["version"]) == ("calibration_labels", "1.0.0")
    assert first["params"]["calibration_set_id"] == "{{calibration_set}}"
    assert json.loads(first["params"]["exclude_versions"]) == [
        {"version_id": "{{train_version}}", "split": "train"}
    ]
    assert "carrier_id" not in json.dumps(stage("human_eval")["recipe"])
    assert second["params"]["output_column"] == "human_label_ex_masked"
    assert second["params"]["default"] == "@human_label"
    value, conditions = parse_rule(second["params"]["rules"][0])
    assert value == "excluded"
    assert all(_holds({"kind": "masked"}[c], op, lit) for c, op, lit in conditions)
    assert not all(_holds({"kind": "pos"}[c], op, lit) for c, op, lit in conditions)
    assert stage("human_eval")["inputs"] == [
        {"kind": "version", "version_id": "{{merged_version}}"}
    ]
    order = [s["id"] for s in load("pipeline.json")["stages"]]
    assert order.index("calibration_labels") < order.index("human_eval") < order.index("train")


def test_the_script_it_replaces_is_gone() -> None:
    assert not (REPO / "backend" / "scripts" / "human_eval_recipe.py").exists()
    assert "human_eval_recipe" not in (PACK / "RUNBOOK.md").read_text(encoding="utf-8")


def test_the_runbook_counts_with_the_tool_it_names() -> None:
    """Step 11's cross-tab and step 8's whole-version check are tools now, not hand reads."""
    runbook = (PACK / "RUNBOOK.md").read_text(encoding="utf-8")
    contract = (REPO / "docs" / "mcp-contract.md").read_text(encoding="utf-8")
    for tool in (
        "dataworks_get_version_crosstab",
        "dataworks_run_version_check",
        "dataworks_get_calibration_label_agreement",
    ):
        assert f"`{tool}`" in runbook, tool
        assert f"`{tool}`" in contract, tool


def test_the_runbook_covers_every_stage() -> None:
    runbook = (PACK / "RUNBOOK.md").read_text(encoding="utf-8")
    for item in load("pipeline.json")["stages"]:
        assert f"`{item['id']}`" in runbook, item["id"]
    assert Path(PACK / "RUNBOOK.md").stat().st_size > 2000


# --- the D128 stack: one probe per form ---------------------------------------------------------


def stack_probes() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = stage("stack")["probes"]
    return found


def render(template: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    """The stage note's rendering: `owns` into {{owns}}, the share's fractions (a JSON list) into
    "{{split_fractions}}"."""
    text = json.dumps(template).replace("{{owns}}", probe["owns"])
    share = probe.get("id_test_share")
    if share is not None:
        fractions = [round(1 - share, 4), share, 0, 0, 0]
        text = text.replace('"{{split_fractions}}"', json.dumps(fractions))
    rendered: dict[str, Any] = json.loads(text)
    return rendered


def rule_label(params: dict[str, Any], row: dict[str, Any]) -> str:
    """rule_label@1 as the operator applies it: the first rule that holds, else the default."""
    for rule in params["rules"]:
        value, conditions = parse_rule(rule)
        if all(_holds(row.get(c), op, lit) for c, op, lit in conditions):
            return value
    return str(params["default"])


def probe_label(probe: dict[str, Any], row: dict[str, Any]) -> str | None:
    """The probe recipe on one row: None when its filter drops the row, else probe_truth."""
    keep, filt, label, _drop = render(stage("stack")["probe_recipe"], probe)["steps"]
    if rule_label(keep["params"], row) in filt["params"]["values"]:
        return None
    return rule_label(label["params"], row)


DISCLOSURE_KINDS = ("pos", "glued_letters")


def test_the_stack_recipes_are_live_operators_with_valid_parameters() -> None:
    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    checked = 0
    for probe in stack_probes():
        templates = ["probe_recipe"] + (["resplit_recipe"] if probe["id_test_share"] else [])
        for name in templates:
            body = filled(render(stage("stack")[name], probe))
            assert "{{" not in json.dumps(body), (probe["id"], name)
            for step in body["steps"]:
                manifest = registry.entry(step["operator"], step["version"]).manifest
                jsonschema.validate(step["params"], manifest.params_schema)
                checked += 1
    assert checked >= 7 * 4 + 4 * 4


def test_every_disclosure_belongs_to_exactly_one_probe() -> None:
    """Additive (D128): a disclosing kind and form no probe owns is a hole in the stack; one two
    probes own is trained twice."""
    vocab = load("plan.json")["vocabulary"]
    assert set(DISCLOSURE_KINDS) <= set(vocab["kinds"])
    for kind in DISCLOSURE_KINDS:
        for form in vocab["forms"]:
            row = {"deployed_truth": True, "kind": kind, "form": form}
            owners = [p["id"] for p in stack_probes() if probe_label(p, row) == "True"]
            assert len(owners) == 1, (kind, form, owners)


def test_each_probe_keeps_every_negative_and_drops_other_forms_disclosures() -> None:
    vocab = load("plan.json")["vocabulary"]
    for probe in stack_probes():
        for kind in vocab["kinds"]:
            for form in vocab["forms"]:
                negative = {"deployed_truth": False, "kind": kind, "form": form}
                assert probe_label(probe, negative) == "False", (probe["id"], kind, form)
        everyday = {"deployed_truth": None, "url": "https://example.org/page"}
        assert probe_label(probe, everyday) == "False", probe["id"]
        # A disclosure of another form is dropped, never relabelled negative (D128).
        others = [
            {"deployed_truth": True, "kind": k, "form": f}
            for k in DISCLOSURE_KINDS
            for f in vocab["forms"]
        ]
        assert any(probe_label(probe, r) is None for r in others), probe["id"]
        assert all(probe_label(probe, r) in (None, "True") for r in others), probe["id"]


def test_an_unlabelled_carrier_reaches_a_value_the_set_refuses() -> None:
    """A carrier with no deployed_truth and no url must not become a silent negative: it reads
    'unlabelled', which the mapping does not map, so D-2 refuses the set."""
    mapping = stage("stack")["set"]["label_mapping"]
    _keep, _filt, label, _drop = stage("stack")["probe_recipe"]["steps"]
    assert rule_label(label["params"], {"deployed_truth": None}) == "unlabelled"
    assert "unlabelled" not in mapping
    # rule_label writes str(True), not 'true': the mapping must use what the operator writes.
    assert set(mapping) == {
        str(parse_rule("true if a == 1")[0]),
        str(parse_rule("false if a == 1")[0]),
    }
    assert mapping == {"True": "positive", "False": "negative"}


def test_the_everyday_columns_leave_before_the_audit_reads_them() -> None:
    """They are set only on everyday rows, so each would predict 'negative' to D-3."""
    _keep, _filt, _label, drop = stage("stack")["probe_recipe"]["steps"]
    patterns = drop["params"]["patterns"]
    for column in ("url", "language", "s_ssn_hit", "s_ssn_cued", "e_probe_keep", "top_langs"):
        assert any(re.fullmatch(p.replace("*", ".*"), column) for p in patterns), column
    for column in ("text", "probe_truth", "deployed_truth", "form", "kind", "pair_id"):
        assert not any(re.fullmatch(p.replace("*", ".*"), column) for p in patterns), column


def test_a_resplit_moves_positives_only_and_keeps_pass2s_held_out_sets() -> None:
    resplit = stage("stack")["resplit_recipe"]["steps"]
    keep = resplit[0]["params"]
    for row in ({"deployed_truth": False, "kind": "pos", "form": "spelled"},):
        probe = next(p for p in stack_probes() if p["id"] == "spelled")
        rendered = render(stage("stack")["resplit_recipe"], probe)["steps"][0]["params"]
        assert rule_label(rendered, row) == "drop"
    assert keep["default"] == "drop"
    split = next(s for s in resplit if s["operator"] == "split")["params"]
    pass2 = step_named("pass2", "split")["params"]
    same = (
        "directing_columns",
        "group_columns",
        "held_out",
        "held_out_split_names",
        "held_out_value_columns",
        "held_out_values",
        "identity_columns",
        "split_names",
        "mode",
    )
    assert {k: split[k] for k in same} == {k: pass2[k] for k in same}
    negatives = stage("stack_negatives")["recipe"]["steps"][0]["params"]
    assert rule_label(negatives, {"deployed_truth": False}) == "keep"
    assert rule_label(negatives, {"deployed_truth": True}) == "drop"


def test_a_resplit_share_puts_20_positives_in_the_in_distribution_test() -> None:
    """miStudio refuses a view under 20 of a class; version 55's train+id_test positives per form
    (spelled 423, spaced 356, mixed_words 330, split_lines 205) times the share must clear it."""
    available = {"spelled": 423, "spaced": 356, "mixed-words": 330, "split-lines": 205}
    shares = {p["id"]: p["id_test_share"] for p in stack_probes() if p["id_test_share"]}
    assert set(shares) == set(available)
    for probe_id, share in shares.items():
        assert 0 < share <= 0.15, probe_id
        assert available[probe_id] * share >= 20, (probe_id, share)


def test_only_the_digits_probe_declares_the_cued_reading() -> None:
    extras = {p["id"]: p.get("label_source_extra", []) for p in stack_probes()}
    assert extras["digits"] == ["r_ssn_cued"]
    assert all(not v for k, v in extras.items() if k != "digits")
    assert (
        "r_ssn_hit"
        in next(p for p in stack_probes() if p["id"] == "digits")["label_source_extra_reason"]
    )
