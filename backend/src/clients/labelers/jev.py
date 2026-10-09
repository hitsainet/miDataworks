# Origin: miDataworks prototype (Onegaishimas/miDataworks) scripts/jev_client.py @ cd461bf
# Mode: port (docs/REUSE.md). Kept unchanged in arithmetic: the `bare-v1` prompt and
# p_true_from_logprobs (add bias[slot], divide by temperature[kind], softmax over the kind's
# slots; a missing answer token raises). Changed: generalised over the template's decision kind
# and returning the whole distribution; the template body replaces the JSON config file.
"""JEV ``bare-v1`` rendering and the scoring probability arithmetic (FR-005.14; FTID 005 3.2).

The parity oracle is the prototype itself: ``tests/unit/labeling/test_jev_parity.py`` embeds
``p_true_from_logprobs`` verbatim and compares on 1,000 seeded pairs to 1e-12.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from .base import RowError


def noul_prompt(state: str, question: str) -> str:
    """Template ``bare-v1`` for a yes/no question, as the model card writes it."""
    return (
        f"[kind] noul\n[state] {state}\n[question] {question}\n[options]\nfalse\ntrue\n[decision]:"
    )


def probability_from_logprobs(
    top: Mapping[str, float],
    *,
    verbalizer_ids: Sequence[int],
    slots: Mapping[str, Sequence[int]],
    bias: Sequence[float],
    temperature: Mapping[str, float],
    kind: str,
) -> list[float]:
    """The distribution over ``kind``'s slots from logprobs keyed ``token_id:<id>``.

    ``[P(false), P(true)]`` for JEV's ``noul``. A missing answer token raises; it is never guessed.
    """
    start, end = int(slots[kind][0]), int(slots[kind][1])
    ids = list(verbalizer_ids[start:end])
    missing = [i for i in ids if f"token_id:{i}" not in top]
    if missing:
        raise RowError(f"the response lacks answer tokens {missing}")
    t = temperature[kind]
    z = [(top[f"token_id:{i}"] + bias[start + k]) / t for k, i in enumerate(ids)]
    peak = max(z)
    weights = [math.exp(x - peak) for x in z]
    total = sum(weights)
    return [w / total for w in weights]
