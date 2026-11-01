"""The SSN pilot pack's element route through a real local build (017 FTASKS 17.2; spec ENH-006
D67, D72; pack record ``0xcc/reviews/017_pilot_pack_2026-10-25.md`` section 4, Q1).

``carrier_plan@1`` draws each row's ``kind`` and ``form``; ``synth_identifier@1`` draws its own
perturbation and delimiter style. D67: make every element follow the plan with no code change.
The route is the pack's own ``recipe.json``: one ``synth_identifier`` step per (perturbation,
style), one ``rule_label`` step copying the column that matches the row's kind and form into
``element``, then ``insert_element@1``, ``slot_siblings@1`` (ENH-006 D89: code-made minimal pairs of
every pos carrier; siblings of a held-out genre or generator are dropped), ``rule_set@1`` and
``derive_labels@1``. The build also runs the pack's own ``split@1.1.0`` parameters (splits.json).

Nothing here is a fixture of the test's own: the plan, the twelve templates, the rule set, the
truth tables and the steps are read from ``behaviours/dlp-ssn-us/``. The production operators, the
real orchestrator and finalize, a real carrier run (fake model) and the saved rule set's pin.
What is asserted is read from the built split files.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from src.operators.native.identifiers import schemes
from tests.integration.behaviour.test_carrier_modes import run_body, started
from tests.integration.behaviour.test_carrier_plan_build import build, index_source
from tests.integration.test_version_build import request, setup, src
from tests.support.behaviour_fixtures import definition
from tests.support.generation_fixtures import Gen
from tests.support.operator_build_driver import RealDriver

PACK = Path(__file__).resolve().parents[4] / "behaviours" / "dlp-ssn-us"
KEY = "dlp-ssn-us"
SLOT_TOKEN = re.compile(r"\[\[SLOT-[0-9A-F]+\]\]")
ROWS = 1500
#: Q1 Gap 1, closed (synth_identifier@1.1.0, wrong_length_layout "style"): a wrong-length value
#: in these forms must carry its delimiter. Before the change every such row was undelimited.
WRONG_LENGTH_LAID = {"digits_dash": "-", "digits_space": " ", "digits_dot": ".", "prose": "-"}
DELIMITED = {"digits_dash": "-", "digits_space": " ", "digits_dot": "."}
SSN = schemes.SCHEMES["ssn_us"]


def carriers_of(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The carriers (slot_siblings@1 leaves ``sibling_variant`` null on them)."""
    return [r for r in rows if r["sibling_variant"] is None]


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACK / name).read_text(encoding="utf-8"))
    return data


def _carrier(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    text = " ".join(str(m.get("content")) for m in messages)
    token = SLOT_TOKEN.search(text)
    assert token, text
    return f"Please file this. The value {token.group(0)} is on the form we sent."


def _pinned(steps: list[dict[str, Any]], version: int) -> list[dict[str, Any]]:
    out = copy.deepcopy(steps)
    for step in out:
        if "rule_set_name" in step["params"]:
            step["params"]["rule_set_version"] = version
    return out


def _core(kind: str, value: str) -> str:
    """A glued_letters value without the letters ``adjacent_letters`` glued to either side (a
    masked value keeps its mask characters)."""
    if kind != "glued_letters":
        return value
    return value.strip("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def kind_followed(kind: str, element: str) -> bool:
    verdict = SSN.check(element, "", "")
    if kind == "pos":
        return verdict.valid and verdict.tier == "medium"
    if kind == "masked":
        return not verdict.valid and any(c in "X*" for c in element)
    if kind == "wrong_length":
        return verdict.reason == "wrong_length"
    if kind == "bad_check":
        return verdict.reason == "bad_range"
    if kind == "glued_letters":
        return verdict.valid and verdict.tier == "low" and _core(kind, element) != element
    raise AssertionError(kind)


def _undelimited(core: str) -> bool:
    return not any(c in SSN.delimiters for c in core)


def form_followed(kind: str, form: str, row: dict[str, Any], renderings: set[str]) -> bool:
    element, core = row["element"], _core(kind, row["element"])
    if form in renderings:
        laid = "-" in core if form == "prose" else _undelimited(core)
        return bool(
            laid
            and row["element_rendering"] == form
            and row["element_recovered"] is True
            and row["element_inserted"] != element
        )
    if row["element_inserted"] != element or row["element_rendering"] is not None:
        return False
    if form == "digits":
        return _undelimited(core)
    sep = DELIMITED[form]
    parts = core.split(sep)
    others = [c for c in core if c in SSN.delimiters and c != sep]
    # 3-2-rest: a wrong-length value's last group takes the difference (123-45-67890)
    return not others and len(parts) == 3 and [len(p) for p in parts[:2]] == [3, 2]


async def _saved_rule_set(client: httpx.AsyncClient) -> dict[str, Any]:
    saved = await client.post("/api/v1/rule-sets", json={"document": _load("rule_set.json")})
    assert saved.status_code == 201, saved.text
    return dict(saved.json())


async def _plan(client: httpx.AsyncClient, driver: RealDriver, data_dir: Path) -> dict[str, Any]:
    plan = _load("plan.json")
    params = {
        "definition": json.dumps(definition(KEY)),
        "vocabulary": json.dumps(plan["vocabulary"]),
        "shares": json.dumps(plan["shares"]),
        "rows": ROWS,
        "templated_share": plan["templated_share"],
        "templated_ceiling": plan["templated_ceiling"],
    }
    step = {"operator": "carrier_plan", "version": "1.0.0", "params": params}
    source = index_source(data_dir, ROWS, f"{KEY}-{ROWS}")
    return await build(client, driver, [step], [src(source)], f"{KEY}-plan")


async def _templates(client: httpx.AsyncClient, pin: dict[str, Any]) -> dict[str, Any]:
    """The pack's twelve templates, saved with its pin (filled) and phrase list: the template
    check runs at save against the saved rule set (FR-017.16, D72)."""
    pack = _load("templates.json")
    phrases = _load("plan.json")["vocabulary"]["phrase_list"]
    ids: dict[str, str] = {}
    for name, template in pack["templates"].items():
        checks = [{**c, "version": pin["version"]} for c in template["check_rule_sets"]]
        assert checks == [pin], (name, checks)
        response = await client.post(
            "/api/v1/generation-templates",
            json={
                "name": name,
                "kind": template["kind"],
                "body": template["body"],
                "behaviour_key": KEY,
                "check_rule_sets": checks,
                "check_phrases": phrases,
            },
        )
        assert response.status_code == 201, response.text
        ids[name] = str(response.json()["id"])
    mapping = pack["template_map"]
    return {
        "column": mapping["column"],
        "templates": {g: ids[n] for g, n in mapping["templates"].items()},
    }


async def _elements_version(
    client: httpx.AsyncClient,
    gen: Gen,
    driver: RealDriver,
    data_dir: Path,
    recipe: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    saved = await _saved_rule_set(client)
    recipe = recipe or _load("recipe.json")
    # D72: the pack pins exactly what the save returns (the version is filled at 17.3)
    assert saved["name"] == recipe["rule_set_pin"]["rule_set_name"]
    assert saved["sha256"] == recipe["rule_set_pin"]["rule_set_sha256"]
    pin = {k: saved[k] for k in ("name", "version", "sha256")}
    plan = await _plan(client, driver, data_dir)
    mapping = await _templates(client, pin)
    gen.fake.gen_answer = _carrier
    run = await started(client, run_body(plan, mapping, "carrier_slot", rule_sets=[pin]))
    assert gen.run_until_done(run["id"]).state == "completed"
    steps = [
        {
            "operator": "dw_generated_rows",
            "version": "1",
            "params": {"generation_run_id": run["id"], "target_type": "detector"},
        },
        *_pinned(recipe["steps"], int(saved["version"])),
        {"operator": "split", "version": "1.1.0", "params": _load("splits.json")["params"]},
    ]
    ds, rev = await setup(
        client, {"format": "dw.recipe/v1", "steps": steps}, name=f"{KEY}-elements"
    )
    response = await request(
        client,
        ds,
        rev,
        [{"kind": "version", "version_id": plan["id"]}],
        seed=1,
        bindings=[{"kind": "generation_run", "id": run["id"]}],
    )
    assert response.status_code == 202, response.text
    status = driver.run(response.json()["job_id"])
    from src.models.job import Job
    from src.models.version import VersionBuild

    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert status == "completed", job.error if job else None
        built = db.get(VersionBuild, response.json()["job_id"])
        assert built is not None and built.version_id is not None
        version_id = str(built.version_id)
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    rows: list[dict[str, Any]] = []
    for split in version["splits"]:
        for row in pq.read_table(data_dir / split["path"]).to_pylist():
            rows.append({**row, "_split": split["name"]})
    return [r for r in rows if r["text"] is not None]


async def test_every_element_follows_the_plans_kind_and_form(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """D67 (Q1): through the pack's own steps, each element is the kind its plan row names in
    every cell, and the form in every cell: wrong_length included (Gap 1 closed, 4.1a) and
    ``mixed_words`` included (Q8 closed by D73: an even digit is followed by a space, so no value
    renders unchanged)."""
    rows = carriers_of(await _elements_version(client, gen, driver, data_dir))
    vocabulary = _load("plan.json")["vocabulary"]
    decoys = set(vocabulary["decoys"])
    renderings = {r["rendering"] for r in _load("rule_set.json")["renderings"]}
    cells: Counter[tuple[str, str]] = Counter()
    failed: set[tuple[str, str, str]] = set()
    laid: Counter[str] = Counter()
    for row in rows:
        assert row["element_present"] is True, row["kind"]
        text = row["text"]
        assert text[row["ins_start"] : row["ins_end"]] == row["element_inserted"]
        kind, form = row["kind"], row["form"]
        if kind in decoys:
            assert row["element"] is None and row["decoy_id"] == kind
            continue
        cells[(kind, form)] += 1
        element = row["element"]
        assert row["decoy_id"] is None and element, (kind, form)
        assert kind_followed(kind, element), (kind, form, element)
        if not form_followed(kind, form, row, renderings):
            failed.add((kind, form, element))
        if kind == "wrong_length" and form in WRONG_LENGTH_LAID:
            laid[form] += 1
            assert WRONG_LENGTH_LAID[form] in element, (form, element)  # Gap 1 closed
    planned = {(k, f) for k in vocabulary["kinds"] if k not in decoys for f in vocabulary["forms"]}
    assert set(cells) == planned, sorted(planned - set(cells))  # every cell was built
    # every kind and every form is followed everywhere (no declared gap remains)
    assert failed == set(), sorted(failed)[:5]
    assert set(laid) == set(WRONG_LENGTH_LAID)  # every such cell was built and checked
    # (whether an all-even mixed_words value occurs depends on the run's row keys; that every
    # value changes is pinned deterministically in tests/unit/test_behaviour_packs.py)
    # the working columns are gone; what the label reads is there
    assert not [c for c in rows[0] if c.startswith("e_")]
    assert {"disclosure", "deployed_truth", "r_ssn_hit", "r_vetoed", "sq_presented"} <= set(rows[0])


async def test_the_rules_fire_only_on_valid_digits_and_the_labels_follow_the_plan(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """The same build, read as the detector set will be: the rule set's hit is exactly the valid
    values the rules can see (renderings hide the value, every other kind never fires), the masked
    veto is exactly the masked values the rules can see, and the label is the plan's (kind,
    framing) on a recovered element."""
    rows = await _elements_version(client, gen, driver, data_dir)
    renderings = {r["rendering"] for r in _load("rule_set.json")["renderings"]}
    positive_framings = {"own_identifier", "someones_identifier"}
    decoys = set(_load("plan.json")["vocabulary"]["decoys"])
    for row in rows:
        kind, form = row["kind"], row["form"]
        # every rendering changes the value it inserts (D73 closed Q8), so the rules see a value
        # exactly when its form is not a rendering
        if form in renderings and kind not in decoys:
            assert row["element_inserted"] != row["element"], (kind, form, row["element"])
        visible = form not in renderings
        # an empty sibling keeps kind pos with nothing in the slot
        expect_hit = kind == "pos" and visible and row["element_present"]
        assert row["r_ssn_hit"] is expect_hit, (kind, form, row["element_inserted"])
        # the veto reads the num span, which a rendering hides: a rendered masked value is not
        # vetoed, so its deployed_truth is its label (R-DLP.13 as written, D68; record O2)
        assert row["r_vetoed"] is (kind == "masked" and visible), (kind, row["element_inserted"])
        assert row["deployed_truth"] is (row["disclosure"] and not row["r_vetoed"])
        expect_label = (
            kind not in decoys and row["framing"] in positive_framings and row["element_recovered"]
        )
        assert row["disclosure"] is expect_label, (kind, row["framing"], form)
    assert Counter(r["disclosure"] for r in rows)[True] > 0


# --- minimal pairs (ENH-006 D89, D91) -----------------------------------------------------------

#: D89: about 70% of training rows belong to a pair; the build must land within 5 points.
PAIRED_TARGET, PAIRED_TOLERANCE = 0.70, 0.05


def _sibling_step() -> dict[str, Any]:
    steps = [s for s in _load("recipe.json")["steps"] if s["operator"] == "slot_siblings"]
    assert len(steps) == 1, "the pack declares exactly one slot_siblings step"
    return dict(steps[0])


def _variant_labels(step: dict[str, Any]) -> set[str]:
    return {
        v["kind"] if v["kind"] == "empty" else f"{v['kind']}:{v['ref']}"
        for v in json.loads(step["params"]["variants"])
    }


def pair_shares(rows: list[dict[str, Any]], label: str) -> tuple[float, float]:
    """(paired share, label-flipping share) of ``rows``: a row is paired when its pair_id holds
    more than one row; it is in a label-flipping pair when it is a sibling whose ``label`` differs
    from its carrier's, or a carrier with at least one such sibling."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["pair_id"], []).append(row)
    paired = flipping = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        paired += len(members)
        (carrier,) = carriers_of(members)
        flips = sum(1 for m in members if m is not carrier and m[label] != carrier[label])
        flipping += flips + (1 if flips else 0)
    return paired / len(rows), flipping / len(rows)


async def test_every_pos_carrier_gets_its_declared_minimal_pairs(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, data_dir: Path
) -> None:
    """D89 through the pack's own steps: every training pos carrier has exactly the declared
    siblings, each the carrier's text with only the element span replaced; the rules label the
    siblings (D91: deployed_truth is the probe's label); siblings never leave training (held-out
    sets stay free-form, D89) and share pair_id with their carrier; about 70% of training rows
    are paired."""
    rows = await _elements_version(client, gen, driver, data_dir)
    step = _sibling_step()
    declared = _variant_labels(step)
    assert step["params"]["kinds"] == ["pos"]
    vocabulary = _load("plan.json")["vocabulary"]
    # every decoy the vocabulary declares and the rule set's masked veto are among the edits
    assert {f"decoy:{d}" for d in vocabulary["decoys"]} | {"empty", "vetoed:masked_value"} == (
        declared
    )
    renderings = {r["rendering"] for r in _load("rule_set.json")["renderings"]}
    decoy_values = {d["id"]: set(d["generator"]["list"]) for d in _load("rule_set.json")["decoys"]}
    splits = _load("splits.json")
    held_genres = set(splits["held_out_genres"])
    carriers = {r["pair_id"]: r for r in carriers_of(rows)}
    assert all(c["pair_id"] == c["carrier_id"] for c in carriers.values())
    siblings: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["sibling_variant"] is not None:
            siblings.setdefault(row["pair_id"], []).append(row)
    made = Counter[str]()
    for pair, members in siblings.items():
        carrier = carriers[pair]
        # D89: test sets stay free-form; siblings sit only beside a training carrier
        assert carrier["genre"] not in held_genres and carrier["kind"] == "pos"
        start, end, text = carrier["ins_start"], carrier["ins_end"], carrier["text"]
        for sib in members:
            made[sib["sibling_variant"]] += 1
            # the same carrier: only the element span differs, the split is the carrier's
            assert sib["carrier_id"] == carrier["carrier_id"] and sib["_split"] == carrier["_split"]
            assert sib["ins_start"] == start
            assert sib["text"][:start] == text[:start]
            assert sib["text"][sib["ins_end"] :] == text[end:]
            assert sib["text"][start : sib["ins_end"]] == sib["element_inserted"]
            assert sib["element_inserted"] != carrier["element_inserted"]
            assert sib["framing"] == carrier["framing"] and sib["form"] == carrier["form"]
            visible = carrier["form"] not in renderings
            variant = sib["sibling_variant"]
            # slot_siblings@1.1.0: the element column holds what the span holds, never the
            # carrier's valid value (no rendering variant is declared, which would keep it)
            assert sib["element"] != carrier["element"], variant
            if variant == "empty":
                assert sib["element_present"] is False and sib["element_inserted"] == ""
                assert sib["element"] is None
                assert sib["disclosure"] is False and sib["deployed_truth"] is False
            elif variant.startswith("decoy:"):
                ref = variant.split(":", 1)[1]
                assert sib["kind"] == ref and sib["decoy_id"] == ref
                # element is the identifier value in the slot: null, as on a decoy carrier
                assert sib["element"] is None and sib["element_inserted"]
                if visible:
                    assert sib["element_inserted"] in decoy_values[ref]
                assert sib["disclosure"] is False and sib["deployed_truth"] is False
            else:
                assert variant == "vetoed:masked_value" and sib["kind"] == "masked"
                assert kind_followed("masked", sib["element"]), sib["form"]
                if sib["element_recovered"]:
                    assert _rendered_back(sib, renderings) == sib["element"]
                # D12: a masked value is labelled by framing; the veto acts in deployed_truth
                # only, and only where the rules can read the digits (not in a rendering)
                assert sib["disclosure"] is carrier["disclosure"]
                assert sib["r_vetoed"] is visible
                assert sib["deployed_truth"] is (carrier["disclosure"] and not visible)
            assert sib["r_ssn_hit"] is False
        assert sorted(s["sibling_variant"] for s in members) == sorted(declared), pair
    # every training pos carrier holding its element got its siblings (none skipped)
    expected = {
        p
        for p, c in carriers.items()
        if c["kind"] == "pos" and c["genre"] not in held_genres and c["element_present"]
    }
    assert set(siblings) == expected
    assert set(made) == declared and len(set(made.values())) == 1, made
    # held-out splits are free-form: no sibling, no pair spans two splits
    training = [r for r in rows if r["_split"] == "train"]
    assert all(r["sibling_variant"] is None for r in rows if r["_split"] != "train")
    assert len({(r["pair_id"], r["_split"]) for r in rows}) == len({r["pair_id"] for r in rows})
    paired, flip_truth = pair_shares(training, "deployed_truth")
    _, flip_disclosure = pair_shares(training, "disclosure")
    print(
        f"training rows {len(training)}, paired {paired:.3f}, label-flipping on deployed_truth "
        f"{flip_truth:.3f}, on disclosure {flip_disclosure:.3f}; siblings {dict(made)}"
    )
    assert abs(paired - PAIRED_TARGET) <= PAIRED_TOLERANCE, paired
    # D91: masked siblings flip deployed_truth where the digits are visible, never disclosure
    assert flip_truth > flip_disclosure > 0


def _rendered_back(row: dict[str, Any], renderings: set[str]) -> str:
    """The masked value a sibling carries, read back from its span (a rendering is undone by the
    rule set's normaliser, as ``element_recovered`` records)."""
    from src.rules import renderings as rendering_rules

    value = str(row["element_inserted"])
    if row["form"] not in renderings:
        return value
    spec = next(r for r in _load("rule_set.json")["renderings"] if r["rendering"] == row["form"])
    return str(rendering_rules.normalise(spec, value))
