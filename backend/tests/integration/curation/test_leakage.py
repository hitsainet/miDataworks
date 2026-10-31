"""The leakage check over stored versions and as a report step (FTASKS 9.4, 9.5)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from src.core.database import sync_session_factory
from src.models import VersionReport
from src.services.curation import api
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version, run_operator

ROLES = {"text": "content", "pair_id": "metadata", "label": "metadata"}


def _headlines() -> pa.Table:
    rows = [
        # two edits of one headline: different texts, one pair_id (Humicroedit's pair)
        {"text": "Trump plans a wall along the border", "pair_id": "h1", "label": "a"},
        {"text": "Trump plans a party along the border", "pair_id": "h1", "label": "b"},
        {"text": "Senate passes the farm bill after long delay", "pair_id": "h2", "label": "a"},
        {"text": "Senate passes the farm bill after long delay", "pair_id": "h3", "label": "b"},
        {"text": "Markets rally as rates hold steady this week", "pair_id": "h4", "label": "a"},
        {"text": "a quiet unrelated row about gardening tips", "pair_id": "h5", "label": "b"},
    ]
    table = with_system_columns(rows)
    splits = ["train", "test", "train", "test", "train", "test"]
    return table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(splits)
    )


def test_group_and_exact_leakage_found_across_train_and_test(
    clean_db: None, data_dir: Path
) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, _headlines(), ROLES, split_roles={"test": True})
        vid = str(v.id)
        result = api.check_leakage(
            [(vid, "train"), (vid, "test")], group_column="pair_id", session=db
        )
    assert result.group_pairs == {"test|train": 1}
    assert result.exact_pairs == {"test|train": 1}
    assert result.basis == "lexical" and result.pairs_artefact is not None
    with sync_session_factory()() as db:
        again = api.check_leakage(
            [(vid, "train"), (vid, "test")], group_column="pair_id", session=db
        )
        assert again.report_id == result.report_id  # stored, not recomputed
        assert db.query(VersionReport).filter(VersionReport.kind == "leakage").count() == 1


def test_leakage_ignores_groups_when_none_is_named(clean_db: None, data_dir: Path) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, _headlines(), ROLES)
        result = api.check_leakage([str(v.id)], session=db)
    assert result.group_pairs == {} and result.exact_pairs == {"test|train": 1}


def test_a_version_with_one_split_has_nothing_to_leak(clean_db: None, data_dir: Path) -> None:
    table = _headlines()
    table = table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array(["train"] * 6)
    )
    with sync_session_factory()() as db:
        v = make_version(db, table, ROLES)
        result = api.check_leakage([str(v.id)], session=db)
    assert result.total == 0 and result.report_id is None


def test_cross_role_inputs_are_sides(clean_db: None, data_dir: Path) -> None:
    with sync_session_factory()() as db:
        a = make_version(db, _headlines(), ROLES)
        b = make_version(db, _headlines(), ROLES)
        result = api.check_leakage(
            [(str(a.id), "train", "training"), (str(b.id), "test", "evaluation")],
            group_column="pair_id",
            session=db,
        )
    assert result.exact_pairs.get("evaluation|training", 0) >= 1


def test_leakage_check_report_operator(data_dir: Path) -> None:
    sink: list[dict] = []
    ran = run_operator(
        "leakage_check", {"group_column": "pair_id"}, _headlines(), ROLES, report_sink=sink
    )
    assert ran.events.num_rows == 0
    assert sink[0]["group_pairs"] == {"test|train": 1}
