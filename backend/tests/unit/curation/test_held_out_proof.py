"""The disjointness proof (017 FTASKS 2.2; FR-017.2; FTDD 017 section 4.4).

Fixtures cross the directing columns (every genre meets every generator), so a proof computed on
the wrong column would differ from the right one."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.services.curation.held_out_files import prove_files
from src.services.curation.held_out_proof import (
    CITATION,
    LISTED,
    DirectedBy,
    Facts,
    count_combos,
    identity,
    prove,
)

COLS = ("genre", "generator")
HELD = {"genre": ("g4",), "generator": ("gen-c",)}


def role(columns: tuple[str, ...]) -> DirectedBy:
    return DirectedBy(
        columns=columns,
        all_columns=COLS,
        held_out_values=HELD,
        identity_columns=("generator",),
        group_columns=("carrier_id",),
    )


GENRE = role(("genre",))
GENERATOR = role(("generator",))
BOTH = role(COLS)

#: Training: genres g1-g3 crossed with gen-a and gen-b@r1, 10 rows each.
TRAIN = {(g, m): 10 for g in ("g1", "g2", "g3") for m in ("gen-a", "gen-b@r1")}
#: The genre split: g4 with the non-held generators.
S_GENRE = {("g4", "gen-a"): 5, ("g4", "gen-b@r2"): 5}


def facts(held: dict, train: dict = TRAIN, **kw: object) -> Facts:
    return Facts(held_out_rows=held, training_rows=train, **kw)  # type: ignore[arg-type]


def test_identity_strips_one_revision_suffix_exactly() -> None:
    assert identity("gen-b@r1") == "gen-b" and identity("gen-b:r1") == "gen-b"
    assert identity("org/model@main") == "org/model" and identity("Gen-B") == "Gen-B"


def test_a_passing_record_carries_h_the_sizes_of_t_and_zero_shared_groups() -> None:
    proof = prove("ood_genre", GENRE, facts(S_GENRE, shared_groups={"carrier_id": 0}))
    assert proof.passes
    out = proof.as_dict()
    assert out["columns"] == ["genre"] and out["held_out_values"] == {"genre": ["g4"]}
    assert out["training_values"] == {"genre": 3}
    assert out["shared_groups"] == {"carrier_id": 0} and out["rows"] == 10
    assert out["intersection"] == [] and out["rule"] == CITATION


def test_one_training_row_with_a_held_out_value_is_refused_naming_value_and_count() -> None:
    train = {**TRAIN, ("g4", "gen-a"): 1}
    proof = prove("ood_genre", GENRE, facts(S_GENRE, train))
    assert not proof.passes
    code, message, details = proof.refusal()
    assert code == "held_out_not_disjoint"
    assert details["value"] == "g4" and details["count"] == 1 and details["column"] == "genre"
    assert "'g4'" in message and "1 row(s)" in message and CITATION in message
    assert details["next"]["tool"] == "dataworks_validate_recipe"


def test_a_held_out_row_with_a_null_directing_value_is_refused() -> None:
    proof = prove("ood_genre", GENRE, facts({**S_GENRE, (None, "gen-a"): 2}))
    code, _, details = proof.refusal()
    assert code == "held_out_value_null" and details["count"] == 2 and proof.null_rows == 2


def test_a_second_revision_of_the_held_out_generator_in_training_is_refused() -> None:
    held = {("g1", "gen-c@r1"): 4}
    assert prove("ood_generator", GENERATOR, facts(held)).passes
    train = {**TRAIN, ("g2", "gen-c@r2"): 3}  # same model, another revision
    proof = prove("ood_generator", GENERATOR, facts(held, train))
    code, _, details = proof.refusal()
    assert code == "held_out_not_disjoint" and details["value"] == "gen-c" and details["count"] == 3


def test_a_row_not_held_out_on_its_column_is_refused() -> None:
    proof = prove("ood_genre", GENRE, facts({**S_GENRE, ("g2", "gen-a"): 1}))
    code, message, details = proof.refusal()
    assert code == "held_out_not_disjoint" and details["count"] == 1
    assert details["values"] == {"genre": "g2", "generator": "gen-a"} and "genre='g2'" in message


def test_the_both_split_needs_two_held_out_columns_per_row() -> None:
    assert prove("ood_both", BOTH, facts({("g4", "gen-c@r1"): 3})).passes
    proof = prove("ood_both", BOTH, facts({("g4", "gen-c@r1"): 3, ("g4", "gen-a"): 1}))
    assert not proof.passes and proof.refusal()[0] == "held_out_not_disjoint"


def test_a_proof_on_the_wrong_column_differs() -> None:
    """The fixture crosses the columns: the genre split holds non-held-out generators, so the
    same rows proved as a generator split fail."""
    assert prove("ood_genre", GENRE, facts(S_GENRE)).passes
    assert not prove("ood_genre", GENERATOR, facts(S_GENRE)).passes


def test_count_combos_counts_rows_per_value_combination() -> None:
    genre = ["g1", "g1", None, "g4"]
    generator = ["a", "a", "b", "c"]
    assert count_combos([genre, generator], [0, 1, 2]) == {("g1", "a"): 2, (None, "b"): 1}


# --- Review R1: the proof over written files (``held_out_files``), where finalize reads it ------


def _parquet(path: Path, rows: list[dict[str, str | None]]) -> Path:
    pq.write_table(pa.Table.from_pylist(rows), path)
    return path


def test_every_training_file_is_read_not_only_the_first(tmp_path: Path) -> None:
    """Review R1 (survivor R3): reading only the first training split's file left a held-out
    value in the SECOND training split unseen - the fixture puts the leak there, and the clean
    first file proves the order matters."""
    genre = DirectedBy(
        columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("g4",)}
    )
    held = _parquet(tmp_path / "ood.parquet", [{"genre": "g4"}] * 3)
    train = _parquet(tmp_path / "train.parquet", [{"genre": "g1"}] * 5)
    val = _parquet(tmp_path / "val.parquet", [{"genre": "g2"}, {"genre": "g4"}])
    leaking = prove_files("ood", genre, [held], {"train": train, "val": val})
    assert not leaking.passes and leaking.intersection == (("genre", "g4", 1),)
    assert prove_files("ood", genre, [held], {"train": train}).passes, "the clean half"


def test_a_held_out_file_without_its_directing_column_is_refused(tmp_path: Path) -> None:
    """Review R1 (survivor R11): a step after the split that drops the directing column leaves
    held-out rows with no value at all. They must count as null rows (refused), never as no
    rows - a proof over zero rows passes, and its split could then hold generated rows."""
    genre = DirectedBy(
        columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("g4",)}
    )
    held = _parquet(tmp_path / "ood.parquet", [{"text": "a"}, {"text": "b"}])
    train = _parquet(tmp_path / "train.parquet", [{"text": "c"}])
    proof = prove_files("ood", genre, [held], {"train": train})
    assert proof.rows == 2 and proof.null_rows == 2 and not proof.passes
    assert proof.refusal()[0] == "held_out_value_null"


# --- ENH-006 D42: near values (case- and whitespace-folded matches), evidence only -------------


def test_near_values_record_case_and_whitespace_variants_and_never_refuse() -> None:
    """Training holds ``Fiction`` (case), ``fiction `` (trailing space), ``sci  fi`` (inner run)
    and the unrelated ``g1``: the proof still PASSES (exact matching decides, TD-017.18) and lists
    exactly the three variants, sorted, with their training rows. Each spelling variant is needed:
    a fold without casefold misses the first, one without whitespace folding the other two."""
    genre = DirectedBy(
        columns=("genre",),
        all_columns=("genre",),
        held_out_values={"genre": ("fiction", "sci fi")},
    )
    facts = Facts(
        {("fiction",): 4, ("sci fi",): 2},
        {("Fiction",): 3, ("fiction ",): 2, ("sci  fi",): 1, ("g1",): 9},
    )
    proof = prove("ood", genre, facts)
    assert proof.passes and proof.intersection == ()
    record = proof.as_dict()
    assert record["near_values"] == [
        {"column": "genre", "value": "fiction", "training_value": "Fiction", "training_rows": 3},
        {"column": "genre", "value": "fiction", "training_value": "fiction ", "training_rows": 2},
        {"column": "genre", "value": "sci fi", "training_value": "sci  fi", "training_rows": 1},
    ]
    assert record["near_values_count"] == 3
    # an exact match is the intersection (refused), never also a near value
    exact = prove("ood", genre, Facts({("fiction",): 1}, {("fiction",): 1, ("FICTION",): 1}))
    assert not exact.passes and [n[2] for n in exact.near] == ["FICTION"]


def test_near_values_on_an_identity_column_compare_without_revision() -> None:
    model = DirectedBy(
        columns=("generator",),
        all_columns=("generator",),
        held_out_values={"generator": ("Gen-C",)},
        identity_columns=("generator",),
    )
    proof = prove("ood", model, Facts({("Gen-C@r1",): 1}, {("gen-c@r2",): 4, ("gen-a",): 1}))
    assert proof.passes
    assert proof.as_dict()["near_values"] == [
        {"column": "generator", "value": "Gen-C", "training_value": "gen-c", "training_rows": 4}
    ]


def test_near_values_are_capped_and_counted() -> None:
    genre = DirectedBy(
        columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("x",)}
    )
    training = {(f"X{' ' * i}",): 1 for i in range(LISTED + 5)}
    record = prove("ood", genre, Facts({("x",): 1}, training)).as_dict()
    assert len(record["near_values"]) == LISTED and record["near_values_count"] == LISTED + 5
    assert record["near_values"] == sorted(
        record["near_values"], key=lambda n: (n["column"], n["value"], n["training_value"])
    )


def test_near_values_do_not_depend_on_the_order_rows_were_counted() -> None:
    """D42 asks for a deterministic ordering: finalize counts rows through a group-by whose order
    is not guaranteed, and the re-check script compares the published list with the recomputed
    one for equality. The same counts in two insertion orders give the same, sorted list."""
    genre = DirectedBy(
        columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("b", "a")}
    )
    counts = [(("B ",), 1), (("A",), 2), ((" b",), 3), (("a ",), 4)]
    one = prove("ood", genre, Facts({("a",): 1}, dict(counts))).as_dict()["near_values"]
    two = prove("ood", genre, Facts({("a",): 1}, dict(reversed(counts)))).as_dict()["near_values"]
    assert one == two
    assert [(n["value"], n["training_value"]) for n in one] == [
        ("a", "A"),
        ("a", "a "),
        ("b", " b"),
        ("b", "B "),
    ]
