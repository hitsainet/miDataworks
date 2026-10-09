"""JEV parity offline (FPRD 005 success criterion 1; FTASKS 6.2).

The oracle is the prototype's ``p_true_from_logprobs`` COPIED here verbatim (origin:
``scripts/jev_client.py`` @ cd461bf) — the test imports nothing from ``scripts/``. The product's
arithmetic must equal it to 1e-12 on the prototype's own cases and on 1,000 seeded random pairs.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

import pytest

from src.clients.labelers.base import RowError
from src.clients.labelers.jev import noul_prompt, probability_from_logprobs
from src.services.decision_template_service import builtin_template_documents


def p_true_from_logprobs(top: dict[str, float], config: dict[str, Any]) -> float:
    """VERBATIM from scripts/jev_client.py (the parity oracle)."""
    start, end = config["slots"]["noul"]
    ids = config["verbalizer_ids"][start:end]
    temperature = config["temperature"]["noul"]
    missing = [i for i in ids if f"token_id:{i}" not in top]
    if missing:
        raise ValueError(f"the response lacks answer tokens {missing}: {top}")
    z = [
        (top[f"token_id:{i}"] + config["bias"][start + k]) / temperature for k, i in enumerate(ids)
    ]
    peak = max(z)
    weights = [math.exp(x - peak) for x in z]
    return weights[1] / sum(weights)


@pytest.fixture(scope="module")
def body() -> dict[str, Any]:
    doc = builtin_template_documents()[0]
    return json.loads(doc.body.model_dump_json())


def product(top: dict[str, float], body: dict[str, Any]) -> float:
    return probability_from_logprobs(
        top,
        verbalizer_ids=body["verbalizer_ids"],
        slots=body["slots"],
        bias=body["bias"],
        temperature=body["temperature"],
        kind=body["decision_kind"],
    )[1]


def test_the_prompt_is_the_cards_template() -> None:
    assert (
        noul_prompt("S", "Q")
        == "[kind] noul\n[state] S\n[question] Q\n[options]\nfalse\ntrue\n[decision]:"
    )


def test_prototype_cases(body: dict[str, Any]) -> None:
    f, t = body["verbalizer_ids"][0], body["verbalizer_ids"][1]
    cases = [
        {f"token_id:{f}": math.log(0.3), f"token_id:{t}": math.log(0.7)},
        {f"token_id:{f}": -1.2, f"token_id:{t}": -0.4},
        {f"token_id:{f}": -1.2 - 37.0, f"token_id:{t}": -0.4 - 37.0},
        {f"token_id:{f}": math.log(0.2), f"token_id:{t}": math.log(0.8)},
    ]
    for top in cases:
        assert abs(product(top, body) - p_true_from_logprobs(top, body)) <= 1e-12


def test_1000_seeded_random_pairs(body: dict[str, Any]) -> None:
    rng = random.Random(20261007)
    f, t = body["verbalizer_ids"][0], body["verbalizer_ids"][1]
    worst = 0.0
    for _ in range(1000):
        top = {f"token_id:{f}": rng.uniform(-30, 0), f"token_id:{t}": rng.uniform(-30, 0)}
        worst = max(worst, abs(product(top, body) - p_true_from_logprobs(top, body)))
    assert worst <= 1e-12


def test_bias_and_temperature_both_matter(body: dict[str, Any]) -> None:
    """A fixture that agreed by construction would be bias 0, temperature 1; the real template
    has neither, so dropping either term changes the answer."""
    assert body["bias"][0] != 0 and body["bias"][1] != 0 and body["temperature"]["noul"] != 1.0


def test_a_missing_answer_token_raises(body: dict[str, Any]) -> None:
    with pytest.raises(RowError, match="lacks answer tokens"):
        product({f"token_id:{body['verbalizer_ids'][1]}": -0.1}, body)
