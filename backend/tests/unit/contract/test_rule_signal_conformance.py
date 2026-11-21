"""Every vector passes through the library, the all-candidates scan and each conformant operator's REAL
``run`` (FR-016.12; FTASKS 3.4, 4.3).

Operators run through 003's executor (``run_operator``) on a pyarrow batch built from the vectors in
their ``CONFORMANT`` scope; expectations come from the cases, never from the library. Declaring an
operator conformant that is not (``regex_match@1``) turns its run red.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from src.operators.native.identifiers import schemes
from src.rules import cases as rule_cases
from src.rules.compose import CompiledRuleSet
from src.rules.conformance import CONFORMANT, NOT_CONFORMANT
from src.rules.validators import resolve
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator

DATA = rule_cases.load_cases()
PRIVATE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "private" / "rule-signal-private-cases.json"
)


def _validator_cases(include_private: bool = True) -> list[tuple[str, dict[str, Any]]]:
    out = [(ref, c) for ref, sec in DATA["validators"].items() for c in sec["cases"]]
    if include_private and PRIVATE.exists():
        private = json.loads(PRIVATE.read_text(encoding="utf-8"))
        out += [(ref, c) for ref, sec in private["validators"].items() for c in sec["cases"]]
    return out


# --- the library ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "case"), _validator_cases(), ids=lambda x: x if isinstance(x, str) else x["id"]
)
def test_validator_vectors_through_the_library(ref: str, case: dict[str, Any]) -> None:
    vid, version = ref.rsplit("@", 1)
    outcome = rule_cases.run_validator_case(resolve(vid, int(version)), case)
    assert outcome.passed, outcome.mismatches


_COMPILED: dict[str, CompiledRuleSet] = {}


def _compiled(doc_id: str) -> CompiledRuleSet:
    if doc_id not in _COMPILED:
        _COMPILED[doc_id] = CompiledRuleSet(rule_cases.rule_document(DATA, {"document": doc_id}))
    return _COMPILED[doc_id]


RULE_CASES = [(kind, c) for kind, cs in DATA["rule_kinds"].items() for c in cs]


@pytest.mark.parametrize(
    ("kind", "case"), RULE_CASES, ids=lambda x: x if isinstance(x, str) else x["id"]
)
def test_rule_kind_vectors_through_the_library(kind: str, case: dict[str, Any]) -> None:
    if kind == "refusals":
        outcome = rule_cases.run_refusal_case(case)
    elif kind == "renderings":
        outcome = rule_cases.run_rendering_case(case)
    else:
        outcome = rule_cases.run_rule_case(_compiled(case["document"]), case)
    assert outcome.passed, outcome.mismatches


@pytest.mark.parametrize("case", DATA["rule_set_identity"], ids=lambda c: c["id"])
def test_rule_set_identity_vectors(case: dict[str, Any]) -> None:
    outcome = rule_cases.run_identity_case(case)
    assert outcome.passed, outcome.mismatches


# --- production operators by their real run ---------------------------------------------------------


def _run(
    name: str, version: str, params: dict[str, Any], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    content = ("text",) if "text" in rows[0] else ("value",)
    table = with_system_columns(rows, content=content, source_id="src-conformance")
    ran = run_operator(name, params, table, dict.fromkeys(content, "content"), version=version)
    out: list[dict[str, Any]] = ran.output.to_pylist()
    return out


def _regex(ref: str) -> list[str]:
    name, version = ref.split("@")
    scope = CONFORMANT.get(ref)
    cases = [
        (c, DATA["rule_documents"][c["document"]])
        for c in DATA["rule_kinds"]["pattern"]
        if scope is None or scope.applies(c, DATA["rule_documents"][c["document"]])
    ]
    assert cases, "no pattern case in scope"
    failures = []
    for case, doc in cases:
        signal = doc["signals"][0]
        params = {
            "column": "text",
            "pattern": signal["pattern"],
            "ignore_case": signal["ignore_case"],
        }
        if version == "2":
            params["fold_fullwidth_ascii"] = signal["fold_fullwidth_ascii"]
        elif signal["fold_fullwidth_ascii"]:
            continue  # @1 has no such option
        row = _run(name, version, params, [{"text": case["input"]["text"]}])[0]
        want = case["expect"]["outputs"]
        got = {
            "hit": row["rx_matched"],
            "hits": row["rx_count"],
            "first": row["rx_first"],
            "spans": row["rx_spans"],
        }
        if got != {k: want[k] for k in got}:
            failures.append(f"{case['id']}: expected {want}, got {got}")
    if version == "2":
        from src.operators.errors import OperatorError, StepFailed

        for case in DATA["rule_kinds"]["refusals"]:
            if "pattern" not in case["refuse"]:
                continue
            try:
                _run(
                    name,
                    version,
                    {
                        "column": "text",
                        "pattern": case["refuse"]["pattern"],
                        "fold_fullwidth_ascii": False,
                    },
                    [{"text": "x"}],
                )
                failures.append(f"{case['id']}: not refused")
            except StepFailed as exc:
                if (exc.details.get("rule"), exc.details.get("position")) != (
                    case["rule"],
                    case.get("at"),
                ):
                    failures.append(f"{case['id']}: refused as {exc.details}")
            except OperatorError as exc:
                # the manifest's maxLength refuses an over-long pattern before run (still refused)
                if case["rule"] != "too_long":
                    failures.append(f"{case['id']}: refused by params validation: {exc}")
    return failures


def _checksum(ref: str) -> list[str]:
    scope = CONFORMANT[ref]
    failures = []
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for vref, case in _validator_cases():
        if scope.applies({**case, "validator": vref}, None):
            groups[
                json.dumps([vref, case.get("params", {}), "text" in case["input"]], sort_keys=True)
            ].append((vref, case))
    assert groups, "no format vector in scope"
    for key, members in groups.items():
        vref, params, scan = json.loads(key)
        scheme = vref.split("@")[0]
        step: dict[str, Any] = {"column": "text" if scan else "value", "scheme": scheme, **params}
        if scan:
            step["mode"] = "scan"
            rows = [{"text": c["input"]["text"]} for _, c in members]
        else:
            step.update(before_column="before", after_column="after")
            rows = [
                {
                    "value": c["input"]["value"],
                    "before": c["input"].get("before", ""),
                    "after": c["input"].get("after", ""),
                }
                for _, c in members
            ]
        for (_, case), row in zip(members, _run("checksum_validate", "1", step, rows), strict=True):
            if scan:
                cands = case["expect"]["candidates"]
                best = None
                for c in cands:  # the documented ranking: first of highest (tier, valid)
                    rank = (schemes.TIER_RANK[c["tier"]], c["valid"])
                    if best is None or rank > best[0]:
                        best = (rank, c)
                want = (
                    {
                        "cv_valid": best[1]["valid"],
                        "cv_reason": best[1]["reason"],
                        "cv_start": best[1]["start"],
                        "cv_end": best[1]["end"],
                    }
                    if best
                    else {
                        "cv_valid": False,
                        "cv_reason": "no_candidate",
                        "cv_start": None,
                        "cv_end": None,
                    }
                )
            else:
                expect = case["expect"]
                want = {"cv_valid": expect["valid"], "cv_reason": expect["reason"]}
                want.update({f"cv_{k}": v for k, v in expect.get("outputs", {}).items()})
            got = {k: row[k] for k in want}
            if got != want:
                failures.append(f"{case['id']}: expected {want}, got {got}")
    return failures


def _proximity(ref: str) -> list[str]:
    scope = CONFORMANT[ref]
    failures = []
    cases = [
        (c, DATA["rule_documents"][c["document"]])
        for c in DATA["rule_kinds"]["proximity"]
        if scope.applies(c, DATA["rule_documents"][c["document"]])
    ]
    assert cases, "no proximity case in scope"
    for case, doc in cases:
        prox = next(s for s in doc["signals"] if s["kind"] == "proximity")
        text = case["input"]["text"]
        anchors = _compiled(case["document"]).signal_results(text)[prox["anchor"]].spans
        want = case["expect"]["outputs"]
        if not anchors:
            got = {"near": False}
        else:
            rows = _run(
                "phrase_proximity",
                "1",
                {
                    "column": "text",
                    "phrases": prox["phrases"],
                    "anchor_start_column": "a_start",
                    "anchor_end_column": "a_end",
                    "max_distance_bytes": prox["max_distance"],
                    "case_sensitive": False,
                },
                [{"text": text, "a_start": a.start, "a_end": a.end} for a in anchors],
            )
            near = [r for r in rows if r["pp_near"]]
            got = {"near": bool(near)}
            if near:
                got.update(
                    distance=near[0]["pp_distance"],
                    phrase=near[0]["pp_phrase"],
                    side=near[0]["pp_side"],
                )
        if got != {k: want[k] for k in got}:
            failures.append(f"{case['id']}: expected {want}, got {got}")
    return failures


RUNNERS: dict[str, Callable[[str], list[str]]] = {
    "regex_match": _regex,
    "checksum_validate": _checksum,
    "phrase_proximity": _proximity,
}


@pytest.mark.parametrize("ref", sorted(CONFORMANT))
def test_each_conformant_operator_passes_its_vectors_by_its_real_run(
    ref: str, data_dir: Path
) -> None:
    assert RUNNERS[ref.split("@")[0]](ref) == []


def test_conformance_covers_the_operators_it_names() -> None:
    assert set(CONFORMANT) == {"regex_match@2", "checksum_validate@1", "phrase_proximity@1"}
    assert set(NOT_CONFORMANT) == {"regex_match@1"}


def test_a_not_conformant_operator_fails_the_vectors(data_dir: Path) -> None:
    """Red-capable: run regex_match@1 as if it were conformant; it must fail some vector."""
    assert _regex("regex_match@1")
