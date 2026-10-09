"""The minimal-pair rules, the 007 mode's shape, the Beat wiring and the bound-run rule
(009 FTASKS 15.1 - 15.3; operator decision 2026-10-07)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.services.detector_sets import minimal_pairs as mp


@pytest.mark.parametrize(
    ("before", "after", "chars", "words"),
    [
        ("the cat is happy", "the cat is happy", 0, 0),
        ("the cat is happy", "the cat is sad", 4, 1),  # h->s (1) + ppy->d (3)
        ("a b c", "a b c d", 2, 1),  # " d" inserted
        ("", "new", 3, 1),
        ("one two three", "", 13, 3),
    ],
)
def test_edit_size(before: str, after: str, chars: int, words: int) -> None:
    assert mp.edit_size(before, after) == mp.EditSize(chars, words)


def test_over_cap_names_the_cap_that_was_exceeded() -> None:
    size = mp.EditSize(chars=10, words=2)
    assert mp.over_cap(size, None, None) is None
    assert mp.over_cap(size, 10, 2) is None, "at the cap is within it"
    assert mp.over_cap(size, 9, None) == "chars"
    assert mp.over_cap(size, None, 1) == "words"


V = mp.JudgeVerdict


@pytest.mark.parametrize(
    ("seed", "counterpart", "verified", "code"),
    [
        (V("yes"), V("no"), True, "verified"),
        (V("yes"), V("yes"), False, "flip_not_verified"),
        (V("no"), V("no"), False, "seed_not_flip_from"),
        (V("yes"), None, False, "judge_missing"),
        (None, V("no"), False, "judge_missing"),
        (V("yes"), V(None), False, "judge_missing"),
        (V("yes"), V("parse_failure"), False, "judge_missing"),
        (V("skipped"), V("no"), False, "judge_missing"),
        (V("yes"), V("no", provisional=True), False, "judge_provisional"),
        (V("yes", provisional=True), V("no"), False, "judge_provisional"),
    ],
)
def test_verify_flip(
    seed: mp.JudgeVerdict | None, counterpart: mp.JudgeVerdict | None, verified: bool, code: str
) -> None:
    check = mp.verify_flip(seed, counterpart, "yes", "no")
    assert (check.verified, check.reason_code) == (verified, code)


def test_pair_id_is_the_seed_row_key() -> None:
    assert mp.pair_id("abc123") == "abc123"


@pytest.mark.parametrize(
    ("reported", "check", "expected"),
    [
        ('profile="humor"; set=sha256:1', "match", 'profile="humor"; set=sha256:1'),
        (None, "unreported", "not reported"),
        (None, "match", "not reported"),
        ("", "unreported", "not reported"),
        (None, "not_applicable", "not applicable"),
    ],
)
def test_steering_state_is_never_read_as_unsteered(
    reported: str | None, check: str, expected: str
) -> None:
    assert mp.steering_state(reported, check) == expected


# --- the 007 mode's shape --------------------------------------------------------------------


def _run(**kw: Any) -> Any:
    from src.schemas.generation import GenerationRunCreate

    body: dict[str, Any] = {
        "mode": "minimal_pairs",
        "input_version_id": "v",
        "prompt_column": "text",
        "seed_splits": ["train"],
        "sample_size": 4,
        "respond_template_id": "gt_1",
        "target_type": "detector",
    }
    body.update(kw)
    return GenerationRunCreate.model_validate(body)


def test_a_minimal_pair_run_has_one_shape() -> None:
    assert _run().mode == "minimal_pairs"
    for bad in (
        {"n_responses": 2},
        {"target_type": "sft"},
        {"expand_template_id": "gt_2"},
        {"respond_template_id": None, "expand_template_id": "gt_2"},
    ):
        with pytest.raises(ValidationError):
            _run(**bad)
    with pytest.raises(ValidationError):
        _run(mode="standard")  # a detector dataset takes generated rows only as minimal pairs


def test_a_chain_flips_between_two_different_verdicts() -> None:
    from src.schemas.minimal_pairs import MinimalPairChainCreate

    body = {
        "input_version_id": "v",
        "text_column": "text",
        "seed_splits": ["train"],
        "sample_size": 2,
        "respond_template_id": "gt_1",
        "rubric_id": "rb_1",
        "flip_from": "yes",
        "flip_to": "yes",
    }
    with pytest.raises(ValidationError):
        MinimalPairChainCreate.model_validate(body)
    assert MinimalPairChainCreate.model_validate({**body, "flip_to": "no"}).flip_to == "no"


# --- the Beat wiring ---------------------------------------------------------------------------


def test_beat_advances_the_chains_with_a_registered_task_on_the_default_queue() -> None:
    from src.core.celery_app import TASK_MODULES, celery_app, route_for

    # Read from the modules a WORKER imports, never by importing the task module here (an import
    # in the test would register the task whatever TASK_MODULES says).
    assert "src.workers.minimal_pair_tasks" in TASK_MODULES
    name = "midataworks.detector_sets.advance_minimal_pair_chains"
    celery_app.loader.import_default_modules()
    assert name in celery_app.tasks
    schedule = celery_app.conf.beat_schedule
    assert any(entry["task"] == name for entry in schedule.values())
    assert route_for(name) == "default"


# --- a build reads only what it binds -------------------------------------------------------


def test_the_detector_operators_refuse_an_unbound_run_in_a_build_with_no_bindings() -> None:
    """Until 2026-10-07 ``bound`` passed whenever the build bound NOTHING (``if ctx.bindings and``),
    so a recipe step could read any label run its build never bound."""
    from src.operators.context import RunContext
    from src.operators.errors import OperatorError
    from src.operators.native.detector.common import bound
    from src.operators.native.detector.hard_negative_miner import MANIFEST

    def ctx(step: str | None, bindings: list[dict[str, Any]]) -> RunContext:
        return RunContext(
            manifest=MANIFEST,
            manifest_hash="0" * 64,
            step_seed=1,
            job_id=None,
            column_roles={},
            rowkey_scheme="dw.rowkey/v1",
            step_execution_id=step,
            bindings=bindings,
        )

    with pytest.raises(OperatorError) as refused:
        bound(ctx("step-1", []), "lr_x")
    assert refused.value.code == "label_run_not_bound"
    with pytest.raises(OperatorError):
        bound(ctx("step-1", [{"kind": "label_run", "id": "lr_y"}]), "lr_x")
    bound(ctx("step-1", [{"kind": "label_run", "id": "lr_x"}]), "lr_x")
    bound(ctx(None, []), "lr_x")  # a preview may read an unbound run, as before
