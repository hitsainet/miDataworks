"""The amended rule is cited (017 FTASKS 2.8; FR-017.6, D27): "R-03.38 as amended, ENH-006 D10".

In the split refusal (the step's own generated-row guard and its proof refusal), the finalize
refusals (the proof refusal finalize raises; ``held_out_contains_generated`` is asserted on a real
build in ``tests/integration/test_version_build.py``) and the D-6 note. Each is driven, never
read from source text.
"""

from __future__ import annotations

import pytest

from src.services.curation.held_out_proof import CITATION, DirectedBy, Facts, prove
from src.services.curation.split_plan import SplitRefusal, SplitSpec, plan_split
from src.services.detector_sets import checks
from tests.unit.detector_sets.test_pure_rules import inputs

GENRE = DirectedBy(columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("g4",)})


def test_the_citation_is_the_amended_rule() -> None:
    assert CITATION == "R-03.38 as amended, ENH-006 D10"


def test_the_split_step_generated_row_refusal_cites_it() -> None:
    specs = [SplitSpec("train", 0.5, False), SplitSpec("test", 0.5, True)]
    with pytest.raises(SplitRefusal) as exc:
        plan_split(["a"] * 10, None, ["generated"] * 10, specs, 1)
    assert exc.value.code == "generated_in_held_out"
    assert CITATION in exc.value.message and exc.value.details["rule"] == CITATION


def test_a_failing_proof_cites_it_for_the_step_and_for_finalize() -> None:
    proof = prove("ood", GENRE, Facts({("g4",): 3}, {("g4",): 1, ("g1",): 5}))
    code, message, details = proof.refusal()
    assert code == "held_out_not_disjoint" and CITATION in message
    assert details["rule"] == CITATION and proof.as_dict()["rule"] == CITATION


def test_the_d6_proof_note_cites_it_and_todays_note_does_not() -> None:
    proof = {"split": "ood_genre", "held_out_values": {"genre": ["g4"]}, "passes": True}
    proved = checks.evaluate(
        inputs(synthetic_rows={"set - ood": 5}, proven_splits={"set - ood": proof})
    )
    (d6,) = [o for o in proved if o.code == "D-6"]
    assert CITATION in d6.reason and CITATION in d6.source and d6.details["proofs"]
    plain = checks.evaluate(inputs(synthetic_rows={"set - ood": 5}))
    (d6,) = [o for o in plain if o.code == "D-6"]
    assert CITATION not in d6.reason and "never expanded (R-03.38)" in d6.reason
