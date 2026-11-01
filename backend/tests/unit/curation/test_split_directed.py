"""``split@1.1.0``: value-directed held-out splits (017 FTASKS 2.3, 2.4, 2.6; FR-017.1 - FR-017.3).

Through 003's real executor and the live operator registry. The fixture crosses its columns:
every genre meets every generator, carriers hold two rows each and pairs link two carriers of one
cell, so a held-out value can only be kept out of training by directing it, and a proof computed on
the wrong column fails.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.core.config import get_settings
from src.operators.errors import StepFailed
from src.services.curation.held_out_proof import CITATION
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import registry, run_operator

GENRES = ("g1", "g2", "g3", "g4")
GENERATORS = ("gen-a", "gen-b@r1", "gen-c@r1")
ROLES = {
    "text": "content",
    "label": "metadata",
    "genre": "metadata",
    "generator": "metadata",
    "topic": "metadata",
    "carrier_id": "metadata",
    "pair_id": "metadata",
}
SPLITS = ["train", "test", "ood_genre", "ood_generator", "ood_both"]
DIRECTED: dict[str, Any] = {
    "split_names": SPLITS,
    "split_fractions": [0.8, 0.2, 0.0, 0.0, 0.0],
    # the in-distribution test split holds generated rows, so it is not held out (FR-002.31)
    "held_out": ["ood_genre", "ood_generator", "ood_both"],
    "stratify_by": ["label"],
    "group_columns": ["carrier_id", "pair_id"],
    "directing_columns": ["genre", "generator"],
    "held_out_values": ["g4", "gen-c"],
    "held_out_value_columns": ["genre", "generator"],
    "identity_columns": ["generator"],
    "held_out_split_names": ["ood_genre", "ood_generator", "ood_both"],
}


def fixture_rows(carriers_per_cell: int = 6, *, generated: bool = True) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for gi, genre in enumerate(GENRES):
        for mi, model in enumerate(GENERATORS):
            for c in range(carriers_per_cell):
                carrier = f"c{gi}{mi}{c}"
                for k in range(2):
                    rows.append(
                        {
                            "text": f"{genre} {model} carrier {c} variant {k}",
                            "label": "pos" if (c + k) % 2 else "neg",
                            "genre": genre,
                            "generator": model,
                            "topic": f"t{(gi + mi) % 3}",
                            "carrier_id": carrier,
                            "pair_id": f"p{gi}{mi}{c // 2}",
                        }
                    )
    table = hp.with_system_columns(rows)
    if generated:
        origin = ["generated" if i % 2 else "source" for i in range(table.num_rows)]
        table = table.set_column(
            table.schema.get_field_index("_dw_origin"), "_dw_origin", pa.array(origin)
        )
    return table


def by_split(table: pa.Table, column: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for v, s in zip(
        table.column(column).to_pylist(), table.column("_dw_split").to_pylist(), strict=True
    ):
        out[s].add(v)
    return out


def test_the_registry_serves_both_versions_and_1_1_0_is_current() -> None:
    reg = registry()
    assert reg.current_version("split") == "1.1.0"
    assert reg.get("split", "1.0.0") is not None and reg.get("split", "1.1.0") is not None


def test_1_1_0_without_new_parameters_assigns_byte_identically_to_1_0_0(data_dir: Path) -> None:
    table = fixture_rows(generated=False)
    params = {
        "split_names": ["train", "test"],
        "split_fractions": [0.7, 0.3],
        "held_out": ["test"],
        "stratify_by": ["label"],
        "group_column": "carrier_id",
    }
    old = run_operator("split", params, table, ROLES, version="1.0.0")
    new = run_operator("split", params, table, ROLES, version="1.1.0")
    assert old.output.column("_dw_split").to_pylist() == new.output.column("_dw_split").to_pylist()
    assert set(old.output.column("_dw_split").to_pylist()) == {"train", "test"}


def spanning(table: pa.Table, group: str) -> int:
    """How many groups of ``group`` sit in more than one split."""
    seen: dict[str, set[str]] = defaultdict(set)
    for g, s in zip(
        table.column(group).to_pylist(), table.column("_dw_split").to_pylist(), strict=True
    ):
        seen[g].add(s)
    return sum(len(v) > 1 for v in seen.values())


@pytest.mark.parametrize("mode", ["reassign", "keep_source"])
def test_a_single_group_named_through_group_columns_is_kept_whole(
    data_dir: Path, mode: str
) -> None:
    """Review R1 F1: ``group_columns: [carrier_id]`` alone (the 1.1.0 spelling, no direction)
    took the 1.0.0 path with ``params`` unchanged, and 1.0.0 reads only ``group_column``, so the
    group was dropped: carriers spanned train and test. It must assign exactly as 1.0.0 with
    ``group_column``, and keep_source must still keep the rows' own splits."""
    table = fixture_rows(carriers_per_cell=12, generated=False)
    base: dict[str, Any] = {
        "split_names": ["train", "test"],
        "split_fractions": [0.7, 0.3],
        "held_out": ["test"],
        "stratify_by": ["label"],
        "mode": mode,
    }
    if mode == "keep_source":
        own = ["test" if i % 4 == 0 else "train" for i in range(table.num_rows)]
        table = table.set_column(
            table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(own)
        )
    old = run_operator(
        "split", {**base, "group_column": "carrier_id"}, table, ROLES, version="1.0.0"
    )
    new = run_operator("split", {**base, "group_columns": ["carrier_id"]}, table, ROLES)
    assert new.output.column("_dw_split").to_pylist() == old.output.column("_dw_split").to_pylist()
    if mode == "keep_source":
        # two group columns in keep_source: the rows keep their splits (it was refused as
        # "fractions add up to 0" by the directed path, which ignores the mode)
        both = run_operator(
            "split", {**base, "group_columns": ["carrier_id", "pair_id"]}, table, ROLES
        )
        assert (
            both.output.column("_dw_split").to_pylist()
            == old.output.column("_dw_split").to_pylist()
        )
    if mode == "reassign":
        assert spanning(new.output, "carrier_id") == 0
        # the fixture must be able to tell the two apart: with no group, carriers DO span
        loose = run_operator("split", base, table, ROLES)
        assert spanning(loose.output, "carrier_id") > 0


def test_no_held_out_value_reaches_training_and_no_group_spans_two_splits(data_dir: Path) -> None:
    sink: list[dict[str, Any]] = []
    ran = run_operator("split", DIRECTED, fixture_rows(), ROLES, report_sink=sink)
    out = ran.output
    genres, models = by_split(out, "genre"), by_split(out, "generator")
    for split in ("train", "test"):
        assert "g4" not in genres[split] and "gen-c@r1" not in models[split], split
    assert genres["ood_genre"] == {"g4"} and "gen-c@r1" not in models["ood_genre"]
    assert models["ood_generator"] == {"gen-c@r1"} and "g4" not in genres["ood_generator"]
    assert genres["ood_both"] == {"g4"} and models["ood_both"] == {"gen-c@r1"}
    for group in ("carrier_id", "pair_id"):
        seen: dict[str, set[str]] = defaultdict(set)
        for g, s in zip(
            out.column(group).to_pylist(), out.column("_dw_split").to_pylist(), strict=True
        ):
            seen[g].add(s)
        assert all(len(v) == 1 for v in seen.values()), group
    # generated rows sit in the directed held-out splits; the step proved each disjoint
    report = sink[0]
    for split in ("ood_genre", "ood_generator", "ood_both"):
        proof = report["held_out_proofs"][split]
        assert proof["passes"] and proof["generated_rows"] > 0, proof
        assert ran.meta["split_roles"][split]["directed_by"]["columns"]
    assert ran.meta["split_roles"]["ood_both"]["directed_by"]["columns"] == ["genre", "generator"]
    assert "directed_by" not in ran.meta["split_roles"]["test"]
    assert report["directed_rows"] == 3 * 6 * 2 + 3 * 6 * 2  # every g4 row, gen-c elsewhere


def test_any_declared_column_directs_the_same_way(data_dir: Path) -> None:
    params = {
        **DIRECTED,
        "split_names": ["train", "test", "ood_topic"],
        "split_fractions": [0.8, 0.2, 0.0],
        "held_out": ["ood_topic"],
        "directing_columns": ["topic"],
        "held_out_values": ["t2"],
        "held_out_value_columns": ["topic"],
        "identity_columns": [],
        "held_out_split_names": ["ood_topic"],
    }
    ran = run_operator("split", params, fixture_rows(), ROLES)
    topics = by_split(ran.output, "topic")
    assert topics["ood_topic"] == {"t2"} and "t2" not in topics["train"] | topics["test"]


def test_a_group_carrying_a_training_value_into_a_held_out_split_is_refused(
    data_dir: Path,
) -> None:
    """A pair linking a g4 carrier to a g1 carrier: the whole unit is directed, and the g1 row
    is not held out on its column, so the step refuses before writing (the fast refusal)."""
    table = fixture_rows(generated=False)
    pairs = table.column("pair_id").to_pylist()
    genres = table.column("genre").to_pylist()
    first_g1 = genres.index("g1")
    pairs[first_g1] = pairs[genres.index("g4")]
    table = table.set_column(table.schema.get_field_index("pair_id"), "pair_id", pa.array(pairs))
    with pytest.raises(StepFailed) as exc:
        run_operator("split", DIRECTED, table, ROLES)
    assert exc.value.code == "held_out_not_disjoint"
    assert exc.value.details["split"] == "ood_genre" and exc.value.details["count"] >= 1
    assert CITATION in str(exc.value)


def test_a_random_held_out_split_still_refuses_generated_rows(data_dir: Path) -> None:
    params = {k: DIRECTED[k] for k in ("stratify_by", "group_columns")}
    params.update(split_names=["train", "test"], split_fractions=[0.8, 0.2], held_out=["test"])
    with pytest.raises(StepFailed) as exc:
        run_operator("split", params, fixture_rows(), ROLES)
    assert exc.value.code == "generated_in_held_out"
    assert exc.value.details["rule"] == CITATION


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"split_fractions": [0.7, 0.2, 0.1, 0.0, 0.0]}, "directed_split_name_conflict"),
        ({"held_out": ["ood_genre", "ood_generator"]}, "directed_split_name_conflict"),
        ({"held_out_split_names": ["ood_genre", "ood_generator"]}, "directed_split_name_conflict"),
        ({"held_out_value_columns": ["genre", "topic"]}, "split_fractions_invalid"),
        ({"identity_columns": ["topic"]}, "split_fractions_invalid"),
    ],
)
def test_the_directing_parameters_are_checked_when_the_recipe_is_validated(
    change: dict[str, Any], code: str
) -> None:
    problems = registry().validate_params("split", "1.1.0", {**DIRECTED, **change})
    assert len(problems) == 1 and code in problems[0], problems


def test_the_held_out_value_cap_is_named_and_the_setting_changes_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = {
        **DIRECTED,
        "held_out_values": ["g3", "g4", "gen-c"],
        "held_out_value_columns": ["genre", "genre", "generator"],
    }
    assert registry().validate_params("split", "1.1.0", params) == []
    monkeypatch.setattr(get_settings(), "behaviour_max_held_out_values", 1)
    (problem,) = registry().validate_params("split", "1.1.0", params)
    assert "cap_exceeded" in problem and "BEHAVIOUR_MAX_HELD_OUT_VALUES" in problem
