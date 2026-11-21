"""D-6's proof note and D-11's cross-version proof (017 FTASKS 2.9, 2.11; FR-017.4, FR-017.8).

Real versions on disk through ``detector_fixtures``; the value-directed split's ``directed_by`` and
proof are written exactly as finalize writes them (``directed_proofs`` over the version's files).
The genre column crosses the label (every genre holds both classes), so a check reading the wrong
column would answer differently.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
from sqlalchemy import text

from src.core.canonical_json import canonical_json
from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.services.curation.held_out_proof import CITATION, DirectedBy
from src.services.version_finalize import directed_proofs
from tests.support import db_factories
from tests.support.curation_fixtures import make_version
from tests.support.detector_fixtures import (
    ROLES,
    _table,
    headline_negatives,
    labelled_rows,
    set_body,
    version,
)

API = "/api/v1"
GENRE_ROLES = {**ROLES, "genre": "metadata"}
HELD = DirectedBy(
    columns=("genre",),
    all_columns=("genre",),
    held_out_values={"genre": ("g4",)},
    group_columns=("pair_id",),
)


def rows(n: int, *, seed: int, tag: str, genres: tuple[str, ...]) -> list[dict[str, Any]]:
    out = labelled_rows(n, seed=seed, tag=tag)
    for i, r in enumerate(out):
        r["genre"] = genres[i % len(genres)]
    return out


def directed_version(
    splits: dict[str, list[dict[str, Any]]],
    *,
    name: str,
    directed: dict[str, DirectedBy],
    generated: set[str] = frozenset(),  # type: ignore[assignment]
) -> str:
    """A completed version whose ``directed`` splits carry finalize's ``directed_by`` and proof;
    rows of the ``generated`` splits are ``_dw_origin = generated``."""
    table = _table(splits)
    origin = [
        "generated" if s in generated else "source" for s in table.column("_dw_split").to_pylist()
    ]
    table = table.set_column(
        table.schema.get_field_index("_dw_origin"), "_dw_origin", pa.array(origin)
    )
    with sync_session_factory()() as db:
        ds = db_factories.dataset(db, name=name)
        db.commit()
        v = make_version(
            db, table, GENRE_ROLES, split_roles=dict.fromkeys(directed, True), dataset=ds
        )
        src = db_factories.source(db, repo_id=f"org/{name}")
        db.commit()
        written = [(s["name"], resolve_under_data_dir(s["path"])) for s in v.splits]
        roles = {
            s["name"]: {"held_out": s["held_out"]}
            | ({"directed_by": directed[s["name"]].as_dict()} if s["name"] in directed else {})
            for s in v.splits
        }
        proofs = directed_proofs(written, roles)
        new_splits = [
            (
                {
                    **s,
                    "directed_by": roles[s["name"]]["directed_by"],
                    "proof": proofs[s["name"]].as_dict(),
                }
                if s["name"] in proofs
                else s
            )
            for s in v.splits
        ]
        manifest = canonical_json({"id": str(v.id), "sources": [{"source_id": src.id}]})
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions DISABLE TRIGGER dw_versions_manifest_hash"))
        v.splits = new_splits
        v.manifest = manifest
        v.manifest_sha256 = hashlib.sha256(manifest).hexdigest()
        db.commit()
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_immutable"))
        db.execute(text("ALTER TABLE dw_versions ENABLE TRIGGER dw_versions_manifest_hash"))
        db.commit()
        return str(v.id)


def body(train: str, train_split: str, test: str, ood: str, ood_split: str, cal: str) -> dict:
    out = set_body({"train": train, "ood": ood, "cal": cal}, name=f"set-{train[:6]}-{ood[:6]}")
    by_role = {r["role"]: r for r in out["roles"]}
    by_role["train"]["split"] = train_split
    by_role["id_test"].update(version_id=test, split="test")
    by_role["ood_eval"].update(split=ood_split, pair_column=None)
    return out


def evaluation(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    """D-11's checked entries for the kept evaluation roles (the calibration entry set aside)."""
    return [c for c in outcome["details"]["checked"] if c["kind"] != "calibration_negatives"]


async def checks_for(client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(f"{API}/detector-sets", json=payload)
    assert created.status_code == 201, created.text
    response = await client.post(f"{API}/detector-sets/{created.json()['id']}/checks")
    assert response.status_code == 200, response.text
    return {o["code"]: o for o in response.json()["outcomes"]}


def kept() -> str:
    """The kept evaluation version: genre g4 held out by value, its rows generated."""
    return directed_version(
        {
            "train": rows(120, seed=1, tag="tr", genres=("g1", "g2", "g3")),
            "test": rows(40, seed=2, tag="te", genres=("g1", "g2", "g3")),
            "ood_genre": rows(60, seed=3, tag="og", genres=("g4",)),
        },
        name="kept-eval",
        directed={"ood_genre": HELD},
        generated={"ood_genre"},
    )


async def test_d6_names_the_proof_and_a_random_split_reads_todays_note(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    v = kept()
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="negatives")
    proved = await checks_for(client, body(v, "train", v, v, "ood_genre", cal))
    d6 = proved["D-6"]
    assert d6["outcome"] == "note" and CITATION in d6["reason"], d6
    assert "proved absent from training" in d6["reason"] and "genre in ['g4']" in d6["reason"]
    assert set(d6["details"]["proofs"]) == set(d6["details"]["synthetic_rows"])
    # the same synthetic rows in a random (not value-directed) held-out split: today's note
    random_split = directed_version(
        {
            "train": rows(120, seed=1, tag="tr2", genres=("g1", "g2", "g3")),
            "test": rows(40, seed=2, tag="te2", genres=("g1", "g2", "g3")),
            "ood_genre": rows(60, seed=3, tag="og2", genres=("g4",)),
        },
        name="random-eval",
        directed={},
        generated={"ood_genre"},
    )
    plain = await checks_for(
        client, body(random_split, "train", random_split, random_split, "ood_genre", cal)
    )
    assert plain["D-6"]["outcome"] == "note" and CITATION not in plain["D-6"]["reason"]
    assert "never expanded (R-03.38)" in plain["D-6"]["reason"]
    # one version for every role: nothing kept from another version, D-11 has nothing to prove
    # one version for every evaluation role: only the kept calibration negatives (another dataset,
    # D42) are checked, and none of their rows is a training row
    assert proved["D-11"]["outcome"] == "green", proved["D-11"]
    (only,) = proved["D-11"]["details"]["checked"]
    assert only["kind"] == "calibration_negatives" and only["shared_rows"] == 0


async def test_d11_refuses_a_kept_role_sharing_a_value_with_new_training(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    v = kept()
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="negatives")
    leaking = version(
        {
            "train": rows(120, seed=11, tag="n1", genres=("g1", "g2", "g4")),
            "test": rows(40, seed=12, tag="n2", genres=("g1", "g2")),
        },
        dataset_name="new-training",
        roles=GENRE_ROLES,
    )
    refused = await checks_for(client, body(leaking, "train", leaking, v, "ood_genre", cal))
    d11 = refused["D-11"]
    assert d11["outcome"] == "refused", d11
    assert d11["details"]["code"] == "cross_version_not_disjoint"
    assert d11["details"]["value"] == "g4" and d11["details"]["count"] == 40
    assert d11["details"]["versions"] == [v, leaking]
    assert "'g4'" in d11["reason"] and "kept-eval v1" in d11["reason"]
    assert "new-training v1" in d11["reason"]
    disjoint = version(
        {
            "train": rows(120, seed=21, tag="d1", genres=("g1", "g2", "g3")),
            "test": rows(40, seed=22, tag="d2", genres=("g1", "g2")),
        },
        dataset_name="new-disjoint",
        roles=GENRE_ROLES,
    )
    passed = await checks_for(client, body(disjoint, "train", disjoint, v, "ood_genre", cal))
    assert passed["D-11"]["outcome"] == "green", passed["D-11"]
    (checked,) = evaluation(passed["D-11"])
    assert checked["columns"] == ["genre"] and checked["intersection"] == []
    assert checked["version_id"] == v and checked["training_version_id"] == disjoint


async def test_d11_refuses_a_training_role_without_the_directing_column(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """Review R1 F2: a new training version with no ``genre`` column read every kept held-out
    value as "absent" (the column reads as null), so D-11 was green whatever rows training held -
    a disjointness claim nothing checked. Missing a directing or group column refuses, naming it.
    The two training versions below hold the SAME rows; only the column differs."""
    v = kept()
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="negatives")
    same = rows(120, seed=31, tag="m1", genres=("g1", "g4"))
    blind = version(
        {
            "train": [{k: r[k] for k in r if k != "genre"} for r in same],
            "test": labelled_rows(40, seed=32, tag="m2"),
        },
        dataset_name="no-genre",
        roles=GENRE_ROLES,
    )
    refused = await checks_for(client, body(blind, "train", blind, v, "ood_genre", cal))
    d11 = refused["D-11"]
    assert d11["outcome"] == "refused", d11
    assert d11["details"]["code"] == "cross_version_not_disjoint"
    assert d11["details"]["missing_columns"] == ["genre"], d11["details"]
    assert "'genre'" in d11["reason"] and "no-genre v1" in d11["reason"]
    (checked,) = evaluation(d11)
    assert checked["intersection"] == [], "the vacuous half: nothing found without the column"
    # the same rows WITH the column: refused on the value, so the column is what decided above
    seen = version(
        {"train": same, "test": rows(40, seed=33, tag="m3", genres=("g1",))},
        dataset_name="with-genre",
        roles=GENRE_ROLES,
    )
    named = (await checks_for(client, body(seen, "train", seen, v, "ood_genre", cal)))["D-11"]
    assert named["outcome"] == "refused" and named["details"]["value"] == "g4", named
    assert "missing_columns" not in named["details"]
    (checked,) = evaluation(named)
    assert checked["missing_columns"] == []


async def test_d11_refuses_kept_calibration_negatives_that_are_training_rows(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """ENH-006 D42 (O2 of Review R1): a kept calibration negatives role is checked against the
    new training rows too. Five of its rows (same text, so the same row key) are copied into the
    new training split: refused, naming the role, the count and both versions. The same training
    rows without the copies pass."""
    v = kept()
    negatives = headline_negatives(150, seed=5)
    cal = version({"train": negatives}, dataset_name="negatives")
    base = rows(120, seed=41, tag="c1", genres=("g1", "g2"))
    copied = [{**n, "genre": "g1"} for n in negatives[:5]]
    leaking = version(
        {"train": base + copied, "test": rows(40, seed=42, tag="c2", genres=("g1",))},
        dataset_name="cal-leak",
        roles=GENRE_ROLES,
    )
    refused = (await checks_for(client, body(leaking, "train", leaking, v, "ood_genre", cal)))[
        "D-11"
    ]
    assert refused["outcome"] == "refused", refused
    details = refused["details"]
    assert details["code"] == "cross_version_not_disjoint" and details["shared_rows"] == 5
    assert "calibration" in details["role"] and details["versions"] == [cal, leaking]
    assert "5 row(s) are training rows of cal-leak v1" in refused["reason"]
    assert details["next"]["tool"] == "dataworks_update_detector_set"
    clean = version(
        {"train": base, "test": rows(40, seed=43, tag="c3", genres=("g1",))},
        dataset_name="cal-clean",
        roles=GENRE_ROLES,
    )
    passed = (await checks_for(client, body(clean, "train", clean, v, "ood_genre", cal)))["D-11"]
    assert passed["outcome"] == "green", passed
    (cal_entry,) = [c for c in passed["details"]["checked"] if c["kind"] == "calibration_negatives"]
    assert cal_entry["shared_rows"] == 0 and cal_entry["version_id"] == cal


async def test_d11_refuses_kept_evaluation_rows_copied_into_new_training(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """ENH-006 D45 (017 FTASKS 2.17): D42's row-key check applies to every kept evaluation role
    bound to another version than training's, value-directed or not. The kept ``id_test`` below is
    a plain (random, not value-directed) test split of another version; five of its rows copied
    verbatim into the new training split are refused, naming the role, the count and both
    versions. The same training rows without the copies pass, and the role is still checked."""
    v = kept()
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="negatives")
    kept_test = rows(40, seed=51, tag="kt", genres=("g1", "g2"))
    plain = version(
        {"train": rows(80, seed=52, tag="kp", genres=("g1", "g2")), "test": kept_test},
        dataset_name="kept-plain",
        roles=GENRE_ROLES,
    )
    base = rows(120, seed=53, tag="e1", genres=("g1", "g2"))

    def payload(train: str) -> dict[str, Any]:
        out = body(train, "train", train, v, "ood_genre", cal)
        test_role = next(r for r in out["roles"] if r["role"] == "id_test")
        test_role.update(version_id=plain, split="test")
        return out

    leaking = version(
        {"train": base + kept_test[:5], "test": rows(40, seed=54, tag="e2", genres=("g1",))},
        dataset_name="eval-leak",
        roles=GENRE_ROLES,
    )
    refused = (await checks_for(client, payload(leaking)))["D-11"]
    assert refused["outcome"] == "refused", refused
    details = refused["details"]
    assert details["code"] == "cross_version_not_disjoint" and details["shared_rows"] == 5
    assert "test" in details["role"] and details["versions"] == [plain, leaking]
    assert "5 row(s) are training rows of eval-leak v1" in refused["reason"]
    assert "kept-plain v1" in refused["reason"]
    assert details["next"]["tool"] == "dataworks_update_detector_set"
    (entry,) = [c for c in details["checked"] if c["kind"] == "id_test"]
    assert entry["columns"] == [] and entry["shared_rows"] == 5, "not value-directed, still checked"
    clean = version(
        {"train": base, "test": rows(40, seed=55, tag="e3", genres=("g1",))},
        dataset_name="eval-clean",
        roles=GENRE_ROLES,
    )
    passed = (await checks_for(client, payload(clean)))["D-11"]
    assert passed["outcome"] == "green", passed
    by_kind = {c["kind"]: c for c in passed["details"]["checked"]}
    assert set(by_kind) == {"id_test", "ood_eval", "calibration_negatives"}, by_kind
    assert by_kind["id_test"]["shared_rows"] == 0 and by_kind["id_test"]["version_id"] == plain
    assert by_kind["ood_eval"]["shared_rows"] == 0


async def test_d11_refuses_kept_plain_ood_rows_copied_into_new_training(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """Review R2 (ENH-006 D45): the row-key check covers a kept ``ood_eval`` role that is NOT
    value-directed too (the D45 test above keeps a plain ``id_test``); five of its rows copied
    verbatim into the new training split are refused, naming the role and the count."""
    cal = version({"train": headline_negatives(150, seed=5)}, dataset_name="negatives")
    ood_rows = rows(40, seed=61, tag="po", genres=("g5",))
    plain = version(
        {"train": rows(60, seed=62, tag="pt", genres=("g1",)), "ood": ood_rows},
        dataset_name="kept-plain-ood",
        roles=GENRE_ROLES,
    )
    base = rows(120, seed=63, tag="n1", genres=("g1", "g2"))
    test = rows(40, seed=64, tag="n2", genres=("g1",))
    leaking = version(
        {"train": base + ood_rows[:5], "test": test}, dataset_name="ood-leak", roles=GENRE_ROLES
    )
    refused = (await checks_for(client, body(leaking, "train", leaking, plain, "ood", cal)))["D-11"]
    assert refused["outcome"] == "refused", refused
    details = refused["details"]
    assert details["code"] == "cross_version_not_disjoint" and details["shared_rows"] == 5
    assert details["versions"] == [plain, leaking]
    (entry,) = [c for c in details["checked"] if c["kind"] == "ood_eval"]
    assert entry["columns"] == [] and entry["shared_rows"] == 5, "not value-directed, checked"
    clean = version({"train": base, "test": test}, dataset_name="ood-clean", roles=GENRE_ROLES)
    passed = (await checks_for(client, body(clean, "train", clean, plain, "ood", cal)))["D-11"]
    assert passed["outcome"] == "green", passed
