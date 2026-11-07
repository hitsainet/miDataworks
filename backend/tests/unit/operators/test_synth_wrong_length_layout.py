"""``synth_identifier@1.1.0``'s ``wrong_length_layout`` (017 pilot, ENH-006 D67; pack record
``0xcc/reviews/017_pilot_pack_2026-10-25.md`` section 4.1, Gap 1).

@1 lays a ``wrong_length`` value out with no delimiter whatever the style, so a planned form with
delimiters could not get them. @1.1.0 adds ``wrong_length_layout``: absent or ``none`` it is @1
row for row (proved against the recordings @1 was pinned to before 016); ``style`` lays the value
out in the row's style, the last group taking the difference.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.core.canonical_json import canonical_sha256
from src.operators.native.identifiers import schemes
from src.operators.registry import OperatorRegistry
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import run_operator
from tests.support.rule_goldens import EXPECTED_PATH, RECIPES_PATH, run_recipe

V11 = "1.1.0"
SPEC: dict[str, Any] = json.loads(RECIPES_PATH.read_text(encoding="utf-8"))
EXPECTED: dict[str, Any] = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
SYNTH_RECIPES = [r for r in SPEC["recipes"] if any(s[0] == "synth_identifier" for s in r["steps"])]


def _as_v11(recipe: dict[str, Any], **extra: Any) -> dict[str, Any]:
    out = copy.deepcopy(recipe)
    for step in out["steps"]:
        if step[0] == "synth_identifier":
            step[1] = V11
            step[2].update(extra)
    return out


def test_there_are_recorded_synth_recipes() -> None:
    assert SYNTH_RECIPES, "no recorded synth_identifier recipe: the equivalence below is vacuous"


@pytest.mark.parametrize("extra", [{}, {"wrong_length_layout": "none"}], ids=["absent", "none"])
@pytest.mark.parametrize("recipe", SYNTH_RECIPES, ids=lambda r: r["id"])
def test_without_the_layout_1_1_0_builds_the_rows_1_built(
    recipe: dict[str, Any], extra: dict[str, Any], data_dir: Path
) -> None:
    """The existing recipes, their synth steps moved to 1.1.0 with the parameter absent (or
    ``none``), rebuild byte-identical rows: every row and row hash equals @1's recording."""
    rows = run_recipe(_as_v11(recipe, **extra), SPEC["inputs"])
    golden = EXPECTED["recipes"][recipe["id"]]
    assert [canonical_sha256(r) for r in rows] == golden["row_hashes"]
    assert rows == golden["rows"]


def _wrong_length_rows(scheme: str, style: str, layout: str | None) -> list[dict[str, Any]]:
    table: pa.Table = with_system_columns([{"idx": i} for i in range(300)], content=("idx",))
    params: dict[str, Any] = {
        "scheme": scheme,
        "seed": 5,
        "valid_share": 0.0,
        "perturbations": ["wrong_length"],
        "delimiters": [style],
    }
    if layout is not None:
        params["wrong_length_layout"] = layout
    ran = run_operator("synth_identifier", params, table, {"idx": "content"}, version=V11)
    out: list[dict[str, Any]] = ran.output.to_pylist()
    return out


@pytest.mark.parametrize(
    ("scheme", "style"),
    [(name, style) for name in ("ssn_us", "card_pan", "aba_routing") for style in ("dash", "space")]
    + [("ssn_us", "dot")],
)
def test_style_lays_a_wrong_length_value_out_in_its_style(
    scheme: str, style: str, data_dir: Path
) -> None:
    """Same digits as with no layout (no random draw changes), now with the style's delimiter
    between the scheme's groups, the last group taking the difference; still wrong_length."""
    plain = _wrong_length_rows(scheme, style, None)
    laid = _wrong_length_rows(scheme, style, "style")
    sep = schemes.STYLES[style]
    rules = schemes.SCHEMES[scheme]
    for a, b in zip(plain, laid, strict=True):
        assert a["perturbation"] == b["perturbation"] == "wrong_length"
        assert sep not in a["value"]
        assert b["value"].replace(sep, "") == a["value"], (a["value"], b["value"])
        parts = b["value"].split(sep)
        sizes = rules.groups(a["value"])
        assert len(parts) >= 2, b["value"]
        assert [len(p) for p in parts[:-1]] == sizes[: len(parts) - 1], (b["value"], sizes)
        assert b["valid"] is False
    if scheme == "ssn_us":
        assert {len(r["value"]) for r in laid} - {6, 7, 8} != set()  # a longer value too
        for row in laid:
            parts = row["value"].split(sep)
            assert [len(p) for p in parts[:2]] == [3, 2], row["value"]
            assert schemes.SCHEMES["ssn_us"].check(row["value"]).reason == "wrong_length"


def test_a_longer_value_never_holds_a_well_formed_one() -> None:
    """``Scheme.layout`` would write 123-45-6789-0 (a well-formed SSN inside); the surplus joins
    the last group instead."""
    ssn = schemes.SCHEMES["ssn_us"]
    assert ssn.layout("1234567890", "dash") == "123-45-6789-0"
    assert schemes.layout_wrong_length(ssn, "1234567890", "dash") == "123-45-67890"
    assert schemes.layout_wrong_length(ssn, "12345678", "dot") == "123.45.678"
    assert schemes.layout_wrong_length(ssn, "1234567", "none") == "1234567"


def test_only_1_1_0_takes_the_parameter_and_1_is_unchanged() -> None:
    registry = OperatorRegistry.build(catalogues=(), entry_points=())
    params = {"scheme": "ssn_us", "wrong_length_layout": "style"}
    assert registry.validate_params("synth_identifier", "1", params) != []
    assert registry.validate_params("synth_identifier", V11, params) == []
    schema = registry.entry("synth_identifier", V11).manifest.params_schema  # type: ignore[union-attr]
    assert schema["properties"]["wrong_length_layout"]["default"] == "none"


@pytest.mark.parametrize("style", ["dash", "space", "dot"])
def test_generate_keeps_a_wrong_length_value_undelimited_unless_asked(style: str) -> None:
    """``schemes.generate``'s own default (every caller but synth_identifier, which passes the
    flag explicitly, e.g. ``tests/unit/rules/test_validators.py``): off, as before 1.1.0. The
    same seed with the flag on gives the same digits in the style (control (b2), review record)."""
    import random

    sep = schemes.STYLES[style]
    for seed in range(50):
        plain = schemes.generate(
            "ssn_us", random.Random(seed), perturbation="wrong_length", style=style
        )
        laid = schemes.generate(
            "ssn_us",
            random.Random(seed),
            perturbation="wrong_length",
            style=style,
            wrong_length_in_style=True,
        )
        assert sep not in plain.value and plain.value.isdigit(), plain.value
        assert sep in laid.value and laid.value.replace(sep, "") == plain.value
