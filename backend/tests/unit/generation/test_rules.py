"""The pure guards, identities and seeds (007 FTASKS 3.1 – 3.4, 3.8)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.services.generation import rules

# --- seeds (3.1, 3.2) ------------------------------------------------------------------------

KEYS = [f"{i:064x}" for i in range(200)]


def test_seeded_selection_is_deterministic_and_differs_across_seeds() -> None:
    a = rules.select_seeds(KEYS, 20, 7)
    assert a == rules.select_seeds(list(reversed(KEYS)), 20, 7), "depends on the set, not order"
    assert a != rules.select_seeds(KEYS, 20, 8)
    assert len(set(a)) == 20 and set(a) <= set(KEYS)


def test_selection_never_exceeds_the_pool() -> None:
    assert sorted(rules.select_seeds(KEYS[:3], 10, 1)) == sorted(KEYS[:3])
    assert rules.select_seeds([], 10, 1) == []


@pytest.mark.parametrize(
    ("origin", "split", "ok"),
    [
        ("source", "train", True),
        ("generated", "train", False),
        ("source", "test", False),
        (None, "train", False),
    ],
)
def test_the_per_row_recheck_admits_only_source_rows_of_seed_splits(
    origin: str | None, split: str, ok: bool
) -> None:
    assert rules.row_is_eligible_seed(origin, split, ["train"]) is ok


def test_response_seed_is_the_same_in_another_process() -> None:
    here = rules.response_seed(42, "k" * 64, 3)
    code = (
        "import os;os.environ.setdefault('X','1');"
        "from src.services.generation import rules;"
        "print(rules.response_seed(42, 'k'*64, 3))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert int(out) == here
    assert 0 <= here < 2**31
    assert rules.response_seed(42, "k" * 64, 4) != here


def test_record_index_orders_seed_then_response_then_side() -> None:
    assert rules.record_index(0, 0, 3) == 0
    assert rules.record_index(1, 2, 3) == 5
    assert rules.record_index(1, 0, 1, "a") == 2 and rules.record_index(1, 0, 1, "b") == 3


# --- body overrides --------------------------------------------------------------------------


def test_body_overrides() -> None:
    kw = {"sae_id": "s", "features": [(1, 2.0)], "profile_name": "p"}
    assert rules.body_overrides("profile", steering_supported=True, is_millm=True, **kw) == {
        "profile": "p"
    }
    assert rules.body_overrides("inline", steering_supported=True, is_millm=True, **kw) == {
        "steering": {"features": [{"index": 1, "strength": 2.0}], "sae_id": "s"}
    }
    assert rules.body_overrides("none", steering_supported=True, is_millm=True, **kw) == {
        "steering": {"features": []}
    }
    assert rules.body_overrides("none", steering_supported=False, is_millm=True, **kw) == {}
    assert rules.body_overrides("profile", steering_supported=True, is_millm=False, **kw) == {}


# --- one axis (3.3, 3.4) -----------------------------------------------------------------------


def inline(
    features: dict[int, float], sae: str = "s", model: str = "m", layer: int = 3
) -> rules.EffectiveSet:
    return rules.EffectiveSet("inline", model_id=model, sae_id=sae, layer=layer, features=features)


def test_identical_sets_are_not_one_axis() -> None:
    with pytest.raises(rules.NotOneAxis) as exc:
        rules.one_axis_or_raise(inline({1: 2.0}), inline({1: 2.0}))
    assert exc.value.code == "NOT_ONE_AXIS" and exc.value.details["differing"] == []


def test_one_index_is_one_axis() -> None:
    assert rules.one_axis_or_raise(inline({1: 2.0, 5: 1.0}), inline({1: 2.0, 5: 3.0})) == 5


def test_two_indices_list_both() -> None:
    with pytest.raises(rules.NotOneAxis) as exc:
        rules.one_axis_or_raise(inline({1: 2.0, 5: 1.0}), inline({1: 3.0, 5: 3.0}))
    assert [d["index"] for d in exc.value.details["differing"]] == [1, 5]


def test_an_absent_index_counts_as_zero() -> None:
    assert rules.one_axis_or_raise(inline({}), inline({7: 6.0})) == 7
    with pytest.raises(rules.NotOneAxis):
        rules.one_axis_or_raise(inline({7: 0.0}), inline({}))


@pytest.mark.parametrize(
    "other",
    [inline({1: 3.0}, sae="t"), inline({1: 3.0}, model="n"), inline({1: 3.0}, layer=4)],
    ids=["sae", "model", "layer"],
)
def test_a_different_sae_model_or_layer_is_refused(other: rules.EffectiveSet) -> None:
    with pytest.raises(rules.NotOneAxis) as exc:
        rules.one_axis_or_raise(inline({1: 2.0}), other)
    assert exc.value.details["not_comparable"]


def test_one_cluster_profile_at_two_intensities_lists_every_member() -> None:
    """P-22: a cluster dial moves every member, so it is never one axis."""
    profile = {
        "id": "p1",
        "name": "humor",
        "model_id": "m",
        "sae_id": "s",
        "layer": 3,
        "steering": {"10": 2.0, "20": -1.0, "30": 4.0},
        "updated_at": "2026-10-07T00:00:00Z",
    }
    a = rules.resolve_effective_set(
        {"kind": "profile", "profile_name": "humor"}, {**profile, "intensity": 1.0}
    )
    b = rules.resolve_effective_set(
        {"kind": "profile", "profile_name": "humor"}, {**profile, "intensity": 2.0}
    )
    with pytest.raises(rules.NotOneAxis) as exc:
        rules.one_axis_or_raise(a, b)
    assert [d["index"] for d in exc.value.details["differing"]] == [10, 20, 30]


def test_profile_effective_values_are_stored_values_times_lambda() -> None:
    profile = {
        "id": "p",
        "name": "n",
        "steering": {"4": 3.0, "9": 0.0},
        "intensity": 0.5,
        "updated_at": "t",
    }
    effective = rules.resolve_effective_set({"kind": "profile", "profile_name": "n"}, profile)
    assert dict(effective.features) == {4: 1.5}


# --- identities and judge independence (3.8) ---------------------------------------------------


def test_identity_reports_missing_revision_and_steering_honestly() -> None:
    ident = rules.generator_identity("m", None, None)
    assert ident.as_dict() == {"model_id": "m", "revision": "not reported", "set_hash": "none"}


def test_two_not_reported_revisions_of_one_model_conflict() -> None:
    judge = rules.generator_identity("m", None, None)
    assert rules.judge_conflicts(judge, [rules.generator_identity("m", None, None)])


def test_one_not_reported_revision_still_conflicts() -> None:
    judge = rules.generator_identity("m", "r1", None)
    assert rules.judge_conflicts(judge, [rules.generator_identity("m", None, None)])


def test_two_reported_different_revisions_do_not_conflict() -> None:
    judge = rules.generator_identity("m", "r1", None)
    assert not rules.judge_conflicts(judge, [rules.generator_identity("m", "r2", None)])


def test_a_different_steering_set_is_a_different_generator() -> None:
    judge = rules.generator_identity("m", "r1", None)
    steered = rules.generator_identity("m", "r1", "sha256:" + "a" * 64)
    assert not rules.judge_conflicts(judge, [steered])


def test_the_endpoint_is_ignored() -> None:
    """The same model on two base URLs is the same model (T-35): identity has no URL at all."""
    judge = rules.generator_identity("m", "r1", None)
    assert rules.judge_conflicts(judge, [rules.generator_identity("m", "r1", None)])
    assert "base_url" not in judge.as_dict()


def test_conflict_error_names_the_inheritance() -> None:
    judge = rules.generator_identity("m", None, None)
    conflicts = rules.judge_conflicts(judge, [judge])
    error = rules.conflict_error(judge, conflicts, inherited_from="judge")
    assert error.code == "JUDGE_IS_GENERATOR" and "inherits the judge" in error.message
    assert error.details["conflicts"] == conflicts


def test_held_out_guard() -> None:
    splits = [{"name": "train", "held_out": False}, {"name": "test", "held_out": True}]
    assert rules.held_out_guard(splits, "v1") == ["test"]
    with pytest.raises(rules.HeldOutMissing) as exc:
        rules.held_out_guard([{"name": "train", "held_out": False}], None)
    assert exc.value.status == 409 and exc.value.details["next_step"]["operator"] == "split"


def test_seed_split_guard() -> None:
    rules.seed_split_guard(["train"], ["test"])
    with pytest.raises(rules.HeldOutSeed) as exc:
        rules.seed_split_guard(["train", "test"], ["test"])
    assert exc.value.details["held_out_seed_splits"] == ["test"]


def test_template_rendering_and_placeholders() -> None:
    assert rules.placeholders("Q: {prompt} ({topic}) {{literal}}") == ["prompt", "topic"]
    assert rules.render_template("Q: {prompt} {{x}}", {"prompt": "hi"}) == "Q: hi {x}"
