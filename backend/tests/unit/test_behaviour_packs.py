"""Every behaviour pack under ``behaviours/`` loads through the operators that will read it (017
FTASKS 17.2; FR-017.13, FR-017.16, FR-017.23 - FR-017.29, FR-017.39; 016 FR-016.8, FR-016.13).

A pack is data, not code (P-25): the files an agent passes, as JSON text, to ``carrier_plan@1``,
``insert_element@1``, ``derive_labels@1``, ``split@1.1.0``, the template check and the
second-opinion label runs. This test runs each file through the real parsers those steps call, so
a pack that the 17.x operator session would have refused at ``dataworks_validate_recipe`` (or that
would build silently wrong rows) is red here first. It names no behaviour: it reads every pack.

Private cases (valid-format synthetic values) live under ``backend/tests/fixtures/private/`` and
are stripped from the public mirror, where that test skips loudly.
"""

from __future__ import annotations

import itertools
import json
import typing
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.operators.native.behaviour.carrier_plan import check_plan_params
from src.operators.native.behaviour.insert_element import check_insert_params
from src.operators.native.curation.split import directed_of
from src.operators.native.identifiers import schemes
from src.operators.registry import OperatorRegistry
from src.rules import cases as rule_cases
from src.rules import public_values
from src.rules import renderings as rendering_rules
from src.rules import rule_set as rule_set_module
from src.rules.compose import CompiledRuleSet
from src.rules.rule_set import GenerateList, RuleSetDocument, SpansValue, parse
from src.schemas.generation import RuleSetPin, TemplateBody, TemplateMap
from src.schemas.labeling import LabelRunStart
from src.services.behaviour.truth_table import parse_truth_tables
from src.services.behaviour.vocabulary import parse_shares, parse_vocabulary
from src.services.generation import template_check
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator
from tests.support.source_tree import REPO, stripped_by_mirror

PACKS = sorted(p for p in (REPO / "behaviours").glob("*") if (p / "behaviour.json").is_file())
PRIVATE = REPO / "backend" / "tests" / "fixtures" / "private" / "behaviours"
#: Every file a pack carries for 17.2 (vocabulary, genres, templates, held-out choices,
#: questions, benchmarks, sizes) beside 016's behaviour.json and rule_set.json.
PACK_FILES = (
    "behaviour.json",
    "rule_set.json",
    "plan.json",
    "labels.json",
    "second_opinion.json",
    "templates.json",
    "splits.json",
    "benchmarks.json",
    "sizes.json",
    "elements.json",
    "recipe.json",
)


def _load(pack: Path, name: str) -> Any:
    return json.loads((pack / name).read_text(encoding="utf-8"))


def _rule_set(pack: Path) -> RuleSetDocument:
    return parse(_load(pack, "rule_set.json"))


def _vocabulary(pack: Path) -> Any:
    return parse_vocabulary(json.dumps(_load(pack, "plan.json")["vocabulary"]))


def _definition(key: str) -> str:
    return json.dumps(
        {"key": key, "definition_version": None, "verified": False, "source": "supplied"}
    )


def test_there_is_a_pack() -> None:
    assert PACKS, "no behaviour pack under behaviours/: this test would check nothing"


@pytest.fixture(params=PACKS, ids=lambda p: p.name)
def pack(request: pytest.FixtureRequest) -> Path:
    path: Path = request.param
    return path


def test_the_pack_has_every_file_and_one_key(pack: Path) -> None:
    missing = [n for n in PACK_FILES if not (pack / n).is_file()]
    assert not missing, missing
    key = _load(pack, "behaviour.json")["behaviour_key"]
    assert key == pack.name
    assert _load(pack, "rule_set.json")["behaviour_key"] == key


def test_every_pack_string_is_public(pack: Path) -> None:
    """D38-2 over EVERY file of the pack, not only rule_set.json: the pack is mirrored publicly,
    so a valid-looking synthetic value anywhere in it would be published."""
    for path in sorted(pack.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert public_values.string_violations(raw) == [], path.name


def test_the_rule_set_passes_its_own_cases(pack: Path) -> None:
    document = _rule_set(pack)
    failed = [(i, o.mismatches) for i, o in rule_cases.run_document_cases(document) if not o.passed]
    assert failed == []
    assert rule_cases.minimum_cases(CompiledRuleSet(document)) == []


def test_the_plan_loads_as_carrier_plan_parameters(pack: Path) -> None:
    plan = _load(pack, "plan.json")
    key = pack.name
    for size in _load(pack, "sizes.json")["plans"]:
        shares = dict(plan["shares"])
        if isinstance(size.get("genre_shares"), dict):
            shares["genre"] = size["genre_shares"]
        params = {
            "definition": _definition(key),
            "vocabulary": json.dumps(plan["vocabulary"]),
            "shares": json.dumps(shares),
            "rows": size["rows"],
            "templated_share": size["templated_share"],
            "templated_ceiling": plan["templated_ceiling"],
        }
        assert check_plan_params(params) == [], size["name"]
        parse_shares(params["shares"], _vocabulary(pack))


def test_decoys_and_renderings_match_the_rule_set(pack: Path) -> None:
    vocabulary = _vocabulary(pack)
    document = _rule_set(pack)
    decoys = {d.id: d for d in document.decoys}
    for kind in vocabulary.decoys:
        assert kind in vocabulary.kinds, kind
        assert isinstance(decoys[kind].generator, GenerateList), kind  # only a list runs (X-30)
    outputs = {o.column for o in document.outputs}
    compiled = CompiledRuleSet(document)
    for decoy in document.decoys:
        assert set(decoy.must_not_fire) <= outputs
        assert isinstance(decoy.generator, GenerateList)
        for item in decoy.generator.items:
            fired = compiled.evaluate(f"value {item} here", None).outputs
            assert not any(fired[c] for c in decoy.must_not_fire), (decoy.id, item)
    styles = _load(pack, "elements.json")["style_by_form"]
    assert set(styles) == set(vocabulary.forms)
    # The voided wallet-card number (the Social Security Administration's published misused
    # number, cited in the pilot rule set's cases): a value the rules DO read valid, so a
    # rendering the rules can still see turns this red. A never-issued value would hide that.
    samples = {"none": "078051120", "dash": "078-05-1120", "space": "078 05 1120"}
    samples["dot"] = "078.05.1120"
    for value in samples.values():
        assert compiled.evaluate(f"number {value} here", None).vetoed is False
        assert any(
            v is True for v in compiled.evaluate(f"number {value} here", None).outputs.values()
        )
    for rendering in _load(pack, "rule_set.json").get("renderings", []):
        name = rendering["rendering"]
        assert name in vocabulary.forms, f"rendering {name} is no declared form"
        value = samples[styles[name]]
        rendered = rendering_rules.render(rendering, value)
        assert rendered != value, f"rendering {name} leaves its {styles[name]} element unchanged"
        assert rendering_rules.normalise(rendering, rendered) == value, name
        hit = compiled.evaluate(f"number {rendered} here", None).outputs
        assert not any(v is True for v in hit.values()), (name, hit)  # invisible to the rules


def test_the_labels_load_and_differ(pack: Path) -> None:
    """derive_labels@1's tables are total and unshadowed, read only declared columns, and no
    sub-question equals the label or another sub-question on every combination (R-DLP.14)."""
    labels = _load(pack, "labels.json")
    vocabulary = _vocabulary(pack)
    tables = parse_truth_tables(json.dumps(labels["truth_tables"]), vocabulary)
    document = _rule_set(pack)
    prefix = labels["output_prefix"]
    spans = {prefix + o.column for o in document.outputs if isinstance(o.value, SpansValue)}
    assert labels["scan_column"] in spans
    assert bool(document.vetoes) == (labels["vetoed_column"] == f"{prefix}vetoed")
    facts = {"element_present", "element_recovered"} | {prefix + o.column for o in document.outputs}
    assert set(tables.fact_inputs()) <= facts
    domains: dict[str, tuple[Any, ...]] = {
        "kind": vocabulary.kinds,
        "framing": vocabulary.framings,
        "form": vocabulary.forms,
        "genre": vocabulary.genres,
    }
    inputs = tables.inputs()
    combos = [
        dict(zip(inputs, values, strict=True))
        for values in itertools.product(*(domains.get(i, (True, False)) for i in inputs))
    ]
    columns = {"label": [tables.label.evaluate(r) for r in combos]}
    first = {sq.name: sq.values[0] for sq in vocabulary.sub_questions}
    for table in tables.sub_questions:
        columns[table.name] = [table.evaluate(r) == first[table.name] for r in combos]
    for a, b in itertools.combinations(columns, 2):
        assert columns[a] != columns[b], f"{a} equals {b} by construction"
    # Hand-labelled rows (R-DLP.10): each answers as the pack says, through the parsed tables.
    by_name = {t.name: t for t in tables.sub_questions}
    for case in labels.get("cases", []):
        row = case["row"]
        for column, expected in case["expect"].items():
            table = tables.label if column == "label" else by_name[column]
            assert table.evaluate(row) == expected, (case["why"], column)


def test_no_framing_phrase_lands_on_a_negative_framing(pack: Path) -> None:
    """``insert_element@1`` puts a phrase from ``phrase_list`` before the element WHATEVER the
    row's framing. When the label table calls some framing of a recovered, non-decoy kind no
    disclosure, a non-zero ``framing_phrase_share`` writes an identifier phrase before numbers
    labelled negative (review 2026-10-25): the share must stay 0 until phrases can be limited."""
    plan = _load(pack, "plan.json")
    vocabulary = _vocabulary(pack)
    tables = parse_truth_tables(json.dumps(_load(pack, "labels.json")["truth_tables"]), vocabulary)
    kinds = [k for k in vocabulary.kinds if k not in vocabulary.decoys]
    negative = sorted(
        {
            framing
            for kind, framing, form in itertools.product(
                kinds, vocabulary.framings, vocabulary.forms
            )
            if not tables.label.evaluate(
                {"kind": kind, "framing": framing, "form": form, "element_recovered": True,
                 "element_present": True}
            )
        }
    )  # fmt: skip
    if negative and vocabulary.phrase_list:
        assert float(plan.get("framing_phrase_share") or 0) == 0, negative


def test_the_questions_cover_the_compared_columns(pack: Path) -> None:
    questions = _load(pack, "second_opinion.json")["questions"]
    labels = _load(pack, "labels.json")
    expected = {labels["label_column"]} | {
        f"sq_{sq.name}" for sq in _vocabulary(pack).sub_questions
    }
    assert set(questions) == expected
    assert all(q.strip().endswith("?") for q in questions.values())
    # the label run's role (LabelRunStart.role), not the endpoint's protocol
    roles = typing.get_args(LabelRunStart.model_fields["role"].annotation)
    assert _load(pack, "second_opinion.json")["role"] in roles


def test_the_templates_pass_the_template_check(pack: Path) -> None:
    """FR-017.16 with the real check: no rule-set signal fires on a template and none holds a
    declared phrase; the map gives every genre one template."""
    data = _load(pack, "templates.json")
    vocabulary = _vocabulary(pack)
    bodies = {}
    for name, template in data["templates"].items():
        assert template["kind"] == "respond"
        bodies[name] = TemplateBody.model_validate(template["body"]).model_dump()
    found = template_check.hits(bodies, [_rule_set(pack)], list(vocabulary.phrase_list))
    assert found == [], [h.as_dict() for h in found]
    mapping = TemplateMap.model_validate(data["template_map"])
    assert mapping.column == "genre"
    assert set(mapping.templates) == set(vocabulary.genres)
    assert set(mapping.templates.values()) <= set(data["templates"])


def test_the_held_out_choices_load_as_split_parameters(pack: Path) -> None:
    splits = _load(pack, "splits.json")
    directed = directed_of(splits["params"])
    assert directed is not None
    vocabulary = _vocabulary(pack)
    assert set(directed.held_out_values["genre"]) == set(splits["held_out_genres"])
    assert set(splits["held_out_genres"]) <= set(vocabulary.genres)
    assert directed.held_out_values["generator"] == (splits["generators"]["held_out"],)
    # TD-017.18: a model id is compared without its revision suffix
    if "generator" in directed.columns:
        assert "generator" in directed.identity_columns
    planned = {p["generator_model_id"] for p in _load(pack, "sizes.json")["plans"]}
    assert planned == {splits["generators"]["training"], splits["generators"]["held_out"]}


def test_the_private_cases_pass_against_the_pack(pack: Path) -> None:
    rel = f"backend/tests/fixtures/private/behaviours/{pack.name}/cases.json"
    path = PRIVATE / pack.name / "cases.json"
    if not path.is_file():
        if stripped_by_mirror(rel):
            pytest.skip(
                "MIRROR: backend/tests/fixtures/private/ is stripped from the public mirror"
            )
        pytest.fail(f"{rel} is missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["behaviour_key"] == pack.name
    document = _rule_set(pack)
    raw = _load(pack, "rule_set.json")
    compiled = CompiledRuleSet(parse({**raw, "cases": data["cases"]}))
    failed = [
        (c.id, rule_cases.run_rule_case(compiled, c).mismatches)
        for c in compiled.document.cases
        if not rule_cases.run_rule_case(compiled, c).passed
    ]
    assert failed == []
    assert document.name == compiled.document.name
    # the private file really carries valid-looking values (else it belongs in the public pack)
    assert public_values.string_violations(data) != []


# --- the element route (D67, Q1), the pin (D72), real text (D70), spend (D66) -------------------


def _pin(pack: Path) -> dict[str, Any]:
    pin: dict[str, Any] = _load(pack, "recipe.json")["rule_set_pin"]
    return pin


def test_the_pack_pins_its_own_rule_set_everywhere(pack: Path) -> None:
    """D72: the pin is THIS rule_set.json (its name and its digest, which the save returns as the
    version's sha256), and every step whose operator reads a stored rule set - read from the live
    registry, not a list kept here - and every template carries that same pin."""
    raw = _load(pack, "rule_set.json")
    pin = _pin(pack)
    assert pin["rule_set_name"] == raw["name"]
    assert pin["rule_set_sha256"] == rule_set_module.digest(raw)
    version = pin["rule_set_version"]
    assert version is None or (isinstance(version, int) and version >= 1), version
    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    readers = 0
    for step in _load(pack, "recipe.json")["steps"]:
        schema = registry.entry(step["operator"], step["version"]).manifest.params_schema
        if "rule_set_sha256" in schema["properties"]:
            readers += 1
            got = {k: step["params"].get(k) for k in pin}
            assert got == pin, step["operator"]
        else:
            assert not set(pin) & set(step["params"]), step["operator"]
    assert readers >= 3  # insert_element, rule_set, derive_labels
    as_template = {
        "name": pin["rule_set_name"],
        "version": version,
        "sha256": pin["rule_set_sha256"],
    }
    for name, template in _load(pack, "templates.json")["templates"].items():
        assert template["check_rule_sets"] == [as_template], name


def test_an_unfilled_pin_cannot_build(pack: Path) -> None:
    """The version is filled at 17.3; until then the registry refuses every pinned step (so a
    recipe saved with the placeholder fails validation, never builds unpinned), and a filled one
    passes. The template pin is refused the same way."""
    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    pin = _pin(pack)
    for step in _load(pack, "recipe.json")["steps"]:
        params = dict(step["params"])
        if "rule_set_name" not in params:
            assert registry.validate_params(step["operator"], step["version"], params) == []
            continue
        if pin["rule_set_version"] is None:
            refused = registry.validate_params(step["operator"], step["version"], params)
            assert any("rule_set_version" in e for e in refused), (step["operator"], refused)
        filled = {**params, "rule_set_version": pin["rule_set_version"] or 1}
        assert registry.validate_params(step["operator"], step["version"], filled) == [], step
    if pin["rule_set_version"] is None:
        with pytest.raises(ValueError):
            RuleSetPin.model_validate(
                {"name": pin["rule_set_name"], "version": None, "sha256": pin["rule_set_sha256"]}
            )


def test_the_label_steps_carry_the_packs_labels_and_plan(pack: Path) -> None:
    labels = _load(pack, "labels.json")
    steps = {s["operator"]: s["params"] for s in _load(pack, "recipe.json")["steps"]}
    derive = steps["derive_labels"]
    assert json.loads(derive["truth_tables"]) == labels["truth_tables"]
    assert json.loads(derive["target_checks"]) == labels["target_checks"]
    for name in ("label_column", "output_prefix", "vetoed_column", "scan_column"):
        assert derive[name] == labels[name], name
    assert steps["rule_set"]["output_prefix"] == labels["output_prefix"]
    plan = _load(pack, "plan.json")
    assert steps["insert_element"]["framing_phrase_share"] == plan["framing_phrase_share"]
    assert steps["insert_element"]["element_column"] == steps["rule_label"]["output_column"]


def _element_rows(pack: Path, per_cell: int) -> list[dict[str, Any]]:
    """Every (kind, form) the vocabulary declares, ``per_cell`` rows each, through the pack's
    synth_identifier and rule_label steps run by the real operators (in memory)."""
    vocabulary = _vocabulary(pack)
    rows = [
        {"idx": i, "kind": kind, "form": form}
        for i, (kind, form, _) in enumerate(
            itertools.product(vocabulary.kinds, vocabulary.forms, range(per_cell))
        )
    ]
    table: pa.Table = with_system_columns(rows, content=("idx",))
    roles = {"idx": "content", "kind": "metadata", "form": "metadata"}
    for step in _load(pack, "recipe.json")["steps"]:
        if step["operator"] not in ("synth_identifier", "rule_label"):
            continue
        ran = run_operator(step["operator"], step["params"], table, roles, version=step["version"])
        table, roles = ran.output, dict(ran.meta["output_column_roles"])
    out: list[dict[str, Any]] = table.to_pylist()
    return out


def test_every_planned_element_is_its_kind_and_form(pack: Path) -> None:
    """D67 (Q1) in memory, every cell: the element a row gets is a value of the synth step whose
    perturbation is the kind's (elements.json by_kind) and whose delimiter style is the form's
    (style_by_form), AND the value itself is laid out in that style (a step that declares a
    style but ignores it, as synth_identifier@1 does for wrong_length, is caught). Decoy kinds
    get no element. (The real-build proof is tests/integration/behaviour/
    test_ssn_pilot_elements.py.)"""
    elements = _load(pack, "elements.json")
    steps = [s for s in _load(pack, "recipe.json")["steps"] if s["operator"] == "synth_identifier"]
    delimiters = schemes.SCHEMES[elements["scheme"]].delimiters
    vocabulary = _vocabulary(pack)
    mismatched: set[tuple[str, str]] = set()
    for row in _element_rows(pack, 3):
        kind, form = row["kind"], row["form"]
        spec = elements["by_kind"][kind]
        if "decoy" in spec:
            assert kind in vocabulary.decoys and row["element"] is None, (kind, form)
            continue
        element = row["element"]
        assert element is not None, (kind, form)
        wanted = {"none"} if spec["valid_share"] == 1 else set(spec["perturbations"])
        sources = [s for s in steps if row[s["params"]["output_prefix"] + "value"] == element]
        made = {
            (row[s["params"]["output_prefix"] + "perturbation"], tuple(s["params"]["delimiters"]))
            for s in sources
        }
        assert any(p in wanted for p, _ in made), (kind, form, element, made)
        style = elements["style_by_form"][form]
        sep = schemes.STYLES[style]
        laid = sep in element if sep else not any(c in delimiters for c in element)
        if not laid or not any(p in wanted and d == (style,) for p, d in made):
            mismatched.add((kind, form))
    assert mismatched == set(), sorted(mismatched)


def test_the_renderings_that_can_leave_a_value_unchanged_are_declared(pack: Path) -> None:
    """A mapping rendering changes only the characters it maps: a value made of unmapped digits
    is inserted as given and the rules read it, although the rendering says it is invisible to
    them. Which renderings can do that is pinned to elements.json known_gaps (record Q8)."""
    elements = _load(pack, "elements.json")
    declared = set(elements.get("known_gaps", {}).get("renders_unchanged", {}))
    separators = {"none": "", "dash": "-", "space": " ", "dot": "."}
    found = set()
    for rendering in _load(pack, "rule_set.json").get("renderings", []):
        sep = separators[elements["style_by_form"][rendering["rendering"]]]
        for digit in "0123456789":
            value = sep.join((digit * 3, digit * 2, digit * 4))  # in the form's own style
            if rendering_rules.render(rendering, value) == value:
                found.add(rendering["rendering"])
    assert found == declared


def test_real_text_carries_positives_and_negatives(pack: Path) -> None:
    """D70: real-text inserts load as insert_element@1 real_text parameters, their shares sum to
    one, at least one is labelled a disclosure and one is not, and a framing phrase goes only
    before a framing the label calls a disclosure (else 'SSN:' would precede a negative)."""
    inserts = _load(pack, "benchmarks.json")["real_text_inserts"]["inserts"]
    vocabulary = _vocabulary(pack)
    plan = _load(pack, "plan.json")
    tables = parse_truth_tables(json.dumps(_load(pack, "labels.json")["truth_tables"]), vocabulary)
    assert sum(Decimal(str(i["share"])) for i in inserts) == 1
    labels = []
    for insert in inserts:
        params = {
            "mode": "real_text",
            "element_column": "value",
            "vocabulary": json.dumps(plan["vocabulary"]),
            "definition": "@input",
            "real_text_values": json.dumps(insert["real_text_values"]),
            "framing_phrase_share": insert["framing_phrase_share"],
        }
        assert check_insert_params(params) == [], insert["name"]
        label = tables.label.evaluate(
            {**insert["real_text_values"], "element_recovered": True, "element_present": True}
        )
        labels.append(label)
        if insert["framing_phrase_share"] > 0:
            assert label is True, insert["name"]
    assert set(labels) == {True, False}


def test_the_second_opinion_fits_the_spend_ceiling(pack: Path) -> None:
    """D66: the price as the role takes it (six decimals) and the ceiling; a full pass (one run per
    compared column, each at the per-run cap) fits under the ceiling."""
    spend = _load(pack, "sizes.json")["second_opinion"]
    price, ceiling = Decimal(str(spend["price_per_call_usd"])), Decimal(
        str(spend["spend_ceiling_usd"])
    )
    assert price == price.quantize(Decimal("0.000001")), "the role's price takes six decimals"
    questions = len(_load(pack, "second_opinion.json")["questions"])
    assert price * spend["per_run_cap_rows"] * questions <= ceiling
