"""Recipe hash, step seeds, step identity, request digest and logical digest (tasks 2.4, 2.5)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.services import identity
from src.services.identity import (
    VERSION_SEED_LIMIT,
    logical_digest,
    logical_digest_of_pairs,
    recipe_hash,
    request_digest,
    step_identity,
    step_seed,
)

BODY = {
    "format": "dw.recipe/v1",
    "steps": [{"operator": "text_length_filter", "version": "1.2", "params": {"min_len": 8}}],
}


class TestRecipeHash:
    def test_hash_is_sha256_of_the_canonical_bytes(self) -> None:
        expected = hashlib.sha256(
            b'{"format":"dw.recipe/v1","steps":[{"operator":"text_length_filter",'
            b'"params":{"min_len":8},"version":"1.2"}]}'
        ).hexdigest()
        assert recipe_hash(BODY) == expected

    def test_key_order_does_not_matter(self) -> None:
        reordered = {"steps": BODY["steps"], "format": "dw.recipe/v1"}
        assert recipe_hash(reordered) == recipe_hash(BODY)

    def test_any_parameter_change_changes_the_hash(self) -> None:
        changed = {**BODY, "steps": [{**BODY["steps"][0], "params": {"min_len": 9}}]}  # type: ignore[dict-item]
        assert recipe_hash(changed) != recipe_hash(BODY)

    def test_operator_version_changes_the_hash(self) -> None:
        changed = {**BODY, "steps": [{**BODY["steps"][0], "version": "1.3"}]}  # type: ignore[dict-item]
        assert recipe_hash(changed) != recipe_hash(BODY)


class TestStepSeed:
    def test_deterministic(self) -> None:
        assert step_seed(20261005, 2, "balance@1") == step_seed(20261005, 2, "balance@1")

    def test_below_two_to_the_32(self) -> None:
        seeds = {step_seed(s, i, "x@1") for s in range(50) for i in range(5)}
        assert all(0 <= s < 2**32 for s in seeds)
        assert len(seeds) == 250

    def test_each_input_changes_it(self) -> None:
        base = step_seed(7, 1, "a@1")
        assert step_seed(8, 1, "a@1") != base
        assert step_seed(7, 2, "a@1") != base
        assert step_seed(7, 1, "a@2") != base

    def test_matches_a_hand_computation(self) -> None:
        raw = hashlib.sha256(b'{"operator":"a@1","seed":7,"step":1}').digest()
        assert step_seed(7, 1, "a@1") == int.from_bytes(raw[:4], "big")


IDENTITY_FIELDS = {
    "input_digest": "i" * 64,
    "kind": "operator",
    "ref": "filter@1",
    "manifest_hash": "m" * 64,
    "params_digest": "p" * 64,
    "seed": 11,
    "bindings": "b" * 64,
    "rowkey_scheme": "dw.rowkey/v1",
}


@pytest.mark.parametrize("field", sorted(IDENTITY_FIELDS))
def test_every_step_identity_field_changes_the_identity(field: str) -> None:
    base = step_identity(**IDENTITY_FIELDS)  # type: ignore[arg-type]
    value = IDENTITY_FIELDS[field]
    changed = dict(IDENTITY_FIELDS, **{field: value + 1 if isinstance(value, int) else value + "x"})
    assert step_identity(**changed) != base  # type: ignore[arg-type]


REQUEST = {
    "dataset_id": "d1",
    "inputs": [{"kind": "source", "source_id": "s1"}, {"kind": "version", "version_id": "v1"}],
    "recipe_hash_hex": "r" * 64,
    "seed": 5,
    "bindings": [{"kind": "label_run", "id": "b"}, {"kind": "label_run", "id": "a"}],
    "rowkey_scheme": "dw.rowkey/v1",
    "column_role_overrides": {},
}


class TestRequestDigest:
    def test_stable_under_list_identity(self) -> None:
        copy = {k: (list(v) if isinstance(v, list) else v) for k, v in REQUEST.items()}
        assert request_digest(**copy) == request_digest(**REQUEST)  # type: ignore[arg-type]

    def test_input_order_matters(self) -> None:
        swapped = dict(REQUEST, inputs=list(reversed(REQUEST["inputs"])))  # type: ignore[call-overload]
        assert request_digest(**swapped) != request_digest(**REQUEST)  # type: ignore[arg-type]

    def test_binding_order_does_not_matter(self) -> None:
        swapped = dict(REQUEST, bindings=list(reversed(REQUEST["bindings"])))  # type: ignore[call-overload]
        assert request_digest(**swapped) == request_digest(**REQUEST)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("dataset_id", "d2"),
            ("recipe_hash_hex", "q" * 64),
            ("seed", 6),
            ("rowkey_scheme", "dw.rowkey/v2"),
            ("column_role_overrides", {"title": "content"}),
            ("bindings", []),
        ],
    )
    def test_each_field_changes_it(self, field: str, value: object) -> None:
        assert request_digest(**dict(REQUEST, **{field: value})) != request_digest(**REQUEST)  # type: ignore[arg-type]


def test_logical_digest_streams_and_matches_a_hand_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = [f"{i:064x}" for i in (3, 1, 3, 2)]
    occ = [0, 0, 1, 0]
    path = tmp_path / "s.parquet"
    pq.write_table(
        pa.table(
            {"_dw_row_key": keys, "_dw_occurrence": pa.array(occ, pa.int32()), "t": ["a"] * 4}
        ),
        path,
        row_group_size=1,
    )
    hand = hashlib.sha256("".join(f"{k}:{o}\n" for k, o in zip(keys, occ, strict=True)).encode())
    assert logical_digest(path) == hand.hexdigest()
    assert logical_digest_of_pairs(list(zip(keys, occ, strict=True))) == hand.hexdigest()
    # order matters
    assert logical_digest_of_pairs(list(zip(reversed(keys), occ, strict=True))) != hand.hexdigest()


def test_version_seed_limit_is_two_to_the_31() -> None:
    assert VERSION_SEED_LIMIT == 2**31
    assert identity.operator_ref("a", "1.2") == "a@1.2"
