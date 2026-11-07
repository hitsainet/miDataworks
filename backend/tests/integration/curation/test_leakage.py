"""The leakage check over stored versions and as a report step (FTASKS 9.4, 9.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
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


# --- identical text under another row number (dry run, 2026-11-01) --------------------------------

INDEXED = {"idx": "content", "text": "content", "label": "metadata"}
WHOLE = "the applicant wrote her number on the second page of the form"


def _indexed(rows: list[tuple[int, str | None, str]]) -> pa.Table:
    """Rows of (idx, text, split) keyed over BOTH content columns, as a build keys them, so identical
    text under another idx has another row key."""
    table = with_system_columns(
        [{"idx": i, "text": t, "label": "a"} for i, t, _ in rows], content=("idx", "text")
    )
    return table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array([s for *_, s in rows])
    )


def _checked(rows: list[tuple[int, str | None, str]], db: Any) -> tuple[Any, Any]:
    version = make_version(db, _indexed(rows), INDEXED, split_roles={"test": True})
    return version, api.check_leakage([str(version.id)], session=db)


def test_identical_text_under_another_index_is_an_exact_pair(
    clean_db: None, data_dir: Path
) -> None:
    """Long and short: with ``idx`` in the compared text, the long pair read near at best and the
    short pair read nothing at all."""
    with sync_session_factory()() as db:
        _, result = _checked(
            [
                (1, WHOLE, "train"),
                (2, WHOLE, "test"),
                (3, "my number", "train"),
                (4, "my number", "test"),
                (5, "only in the training split, not elsewhere", "train"),
                (6, "only in the test split and nowhere else at all", "test"),
            ],
            db,
        )
        assert result.exact_pairs == {"test|train": 2}
        assert result.near_pairs == {}
        pairs = pq.read_table(resolve_under_data_dir(result.pairs_artefact)).to_pylist()
    assert {p["excerpt_a"] for p in pairs} == {WHOLE, "my number"}  # the excerpt holds no index
    assert all(p["row_key_a"] != p["row_key_b"] for p in pairs)
    assert all(len(p["shared"]) == 64 for p in pairs)


def test_each_row_of_a_text_on_several_rows_is_paired_across_sides(
    clean_db: None, data_dir: Path
) -> None:
    """Every crossing row must be in a pair, because the repair drops only rows a pair names. Same
    side copies are not pairs."""
    with sync_session_factory()() as db:
        _, result = _checked(
            [
                (1, WHOLE, "train"),
                (2, WHOLE, "train"),
                (3, WHOLE, "test"),
                (4, WHOLE, "test"),
                (5, "other", "train"),
            ],
            db,
        )
    assert result.exact_pairs == {"test|train": 4} and result.near_pairs == {}


def test_blank_text_is_not_a_duplicate_of_blank_text(clean_db: None, data_dir: Path) -> None:
    """Rows with nothing in the text column (a carrier plan's) are different rows: every blank has
    the one empty shingle, so they would all read near 1.0, and identical once ``idx`` is out."""
    with sync_session_factory()() as db:
        _, result = _checked(
            [(1, None, "train"), (2, None, "test"), (3, "", "train"), (4, "   ", "test")], db
        )
        assert result.total == 0
        # two blanks that differ as strings: only the near check could pair these
        _, result = _checked([(5, "", "train"), (6, "   ", "test")], db)
    assert result.total == 0


def test_a_short_text_near_only_when_its_words_match(clean_db: None, data_dir: Path) -> None:
    """Under one shingle a text is one shingle: the same words (another case, other punctuation)
    are near at 1.0, and any other word is not."""
    with sync_session_factory()() as db:
        _, result = _checked(
            [
                (1, "My number, please", "train"),
                (2, "my number please", "test"),
                (3, "their other number", "test"),
            ],
            db,
        )
    assert result.near_pairs == {"test|train": 1} and result.exact_pairs == {}


def test_a_numeric_only_content_compares_by_key_alone(clean_db: None, data_dir: Path) -> None:
    """A version whose content is only a number: same key is exact, and nothing else is a pair."""
    table = with_system_columns(
        [{"idx": 7, "label": "a"}, {"idx": 7, "label": "a"}, {"idx": 8, "label": "a"}],
        content=("idx",),
    )
    table = table.set_column(
        table.schema.get_field_index("_dw_split"),
        "_dw_split",
        pa.array(["train", "test", "test"]),
    )
    with sync_session_factory()() as db:
        v = make_version(db, table, {"idx": "content", "label": "metadata"})
        result = api.check_leakage([str(v.id)], session=db)
    assert result.exact_pairs == {"test|train": 1} and result.near_pairs == {}


def test_a_report_stored_before_this_comparison_does_not_answer(
    clean_db: None, data_dir: Path
) -> None:
    """The real path: a report stored under the old params (no ``comparison``), which said green,
    is not what D-4 and C-3 read now."""
    from src.services.curation import leakage_service, report_service
    from src.services.curation.codes import ReportInput

    with sync_session_factory()() as db:
        version = make_version(
            db,
            _indexed([(1, "my number", "train"), (2, "my number", "test")]),
            INDEXED,
            split_roles={"test": True},
        )
        vid = str(version.id)
        old = leakage_service.leakage_params(None, None)
        old.pop("comparison")

        def said_green(*_: Any) -> Any:
            return report_service.Computed(
                result={
                    "exact_pairs": {},
                    "near_pairs": {},
                    "group_pairs": {},
                    "basis": "lexical",
                    "threshold": 0.8,
                    "n_rows": 2,
                },
                artefacts=[],
                rows=2,
            )

        _, stored = report_service.find_or_run_inline(
            "leakage",
            [ReportInput(vid)],
            old,
            int(version.seed),
            said_green,
            started_by="test",
            origin="operator",
        )
        result = api.check_leakage([vid], session=db)
        assert result.report_id != stored.id
        assert result.exact_pairs == {"test|train": 1}
        assert db.query(VersionReport).filter(VersionReport.kind == "leakage").count() == 2


def test_the_report_operator_compares_text_too(data_dir: Path) -> None:
    sink: list[dict] = []
    run_operator(
        "leakage_check",
        {},
        _indexed([(1, "my number", "train"), (2, "my number", "test")]),
        INDEXED,
        report_sink=sink,
    )
    assert sink[0]["exact_pairs"] == {"test|train": 1}
