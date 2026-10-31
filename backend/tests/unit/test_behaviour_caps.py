"""The five behaviour caps (017 FTASKS 11.2; FR-017.54), one test file.

For each cap, a request ONE over the setting is refused ``cap_exceeded`` naming the setting, its
limit and the count; raising the setting by one lets the same request through. The caps are read
from ``get_settings()`` at the moment of the check (runtime settings, 012), so the test changes
the value the way the Settings screen does. The fifth, review queues per hand-off, needs a run's
labels: ``tests/integration/review/test_disagreement_handoff.py`` refuses one over through the real
hand-off and writes nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from src.core.config import get_settings
from src.core.errors import AppError
from src.operators.native.curation.split import directed_of
from src.services.behaviour.vocabulary import parse_vocabulary
from src.services.detector_sets.send_service import _group_cap
from tests.support.behaviour_fixtures import BEHAVIOURS, plan_document


def vocabulary(key: str, **over: Any) -> str:
    raw = dict(plan_document(key)["vocabulary"])
    raw.update(over)
    return json.dumps(raw)


def split_params(values: int) -> dict[str, Any]:
    held = [f"g{i}" for i in range(values)]
    return {
        "split_names": ["train", "ood_genre"],
        "split_fractions": [1.0, 0.0],
        "held_out": ["ood_genre"],
        "directing_columns": ["genre"],
        "held_out_values": held,
        "held_out_value_columns": ["genre"] * values,
        "held_out_split_names": ["ood_genre"],
    }


def refused(call: Callable[[], Any]) -> dict[str, Any]:
    with pytest.raises(Exception) as caught:  # noqa: PT011 - each cap has its own error class
        call()
    error = caught.value
    code = getattr(error, "code", None)
    details = getattr(error, "details", None)
    assert code == "cap_exceeded", (type(error), code, details)
    assert isinstance(details, dict)
    return details


def cases(key: str) -> list[tuple[str, str, Callable[[int], Callable[[], Any]], int]]:
    """(attribute, SETTING, request of size n, the size one over the cap the test sets)."""
    raw = plan_document(key)["vocabulary"]
    # one over the largest list the behaviour already declares, so only genres crosses the cap
    widest = max(len(v) for v in raw.values() if isinstance(v, list)) + 1
    first_sq = (raw.get("sub_questions") or [{"name": "a", "values": ["x", "y"]}])[0]
    return [
        (
            "behaviour_max_vocabulary_values",
            "BEHAVIOUR_MAX_VOCABULARY_VALUES",
            lambda n: lambda: parse_vocabulary(vocabulary(key, genres=[f"q{i}" for i in range(n)])),
            widest,
        ),
        (
            "behaviour_max_subquestions",
            "BEHAVIOUR_MAX_SUBQUESTIONS",
            lambda n: lambda: parse_vocabulary(
                vocabulary(
                    key,
                    sub_questions=[{**first_sq, "name": f"sq{i}"} for i in range(n)],
                )
            ),
            3,
        ),
        (
            "behaviour_max_held_out_values",
            "BEHAVIOUR_MAX_HELD_OUT_VALUES",
            lambda n: lambda: directed_of(split_params(n)),
            3,
        ),
        (
            "detector_max_sets_per_send",
            "DETECTOR_MAX_SETS_PER_SEND",
            lambda n: lambda: _group_cap([f"dts_{i}" for i in range(n)]),
            3,
        ),
    ]


@pytest.mark.parametrize("key", BEHAVIOURS)
@pytest.mark.parametrize("index", range(4))
def test_one_over_is_refused_naming_the_setting_and_the_setting_moves_it(
    key: str, index: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    attribute, setting, request, over = cases(key)[index]
    monkeypatch.setattr(get_settings(), attribute, over - 1)
    details = refused(request(over))
    assert (details["setting"], details["limit"], details["actual"]) == (setting, over - 1, over)
    # the same request under a cap one higher is not refused for its size
    monkeypatch.setattr(get_settings(), attribute, over)
    try:
        request(over)()
    except AppError as error:  # another rule may still refuse it, never this cap
        assert error.code != "cap_exceeded", error
    except Exception as error:  # noqa: BLE001 - the operators' own error classes
        assert getattr(error, "code", None) != "cap_exceeded", error
