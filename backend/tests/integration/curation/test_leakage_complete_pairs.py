"""Every near pair above the threshold, so one repair is proof (FU-7a gap (p), 2026-11-27).

CHK1 (fu7a v36) reported 61 near pairs; ``drop_leakage_pairs`` removed every reported fresh row, and
CHK2 on the result found 6 NEW near pairs on rows that were already near-duplicates of the same
train rows (``[Social Security Number]\\nSSN = `` against one train row). Two causes, both in the
candidate step: ``minhash.candidate_pairs`` linked each band bucket's members to its FIRST member
only, so of two fresh rows near one train row only the first was ever compared with it; and the
bands were laid out for deduplication (S-curve midpoint 0.878 for a 0.8 threshold), so a pair AT the
threshold was a candidate one time in five. The fixtures below are the live texts' shape: different
strings with the same words, so the estimate is 1.0 and nothing but the candidate step can miss them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models import VersionReport
from src.services.curation import api, leakage_service
from src.services.identity import file_sha256
from tests.fixtures.humor_pool import with_system_columns
from tests.support.curation_fixtures import make_version, run_operator

ROLES = {"text": "content", "label": "metadata"}

#: One near-duplicate cluster across two splits (the words of all four agree; the strings differ).
CLUSTER = [
    ("[Social Security Number]\nSSN = ", "fresh"),
    ("Social Security Number: SSN =", "fresh"),
    ("social security number ssn", "train"),
    ("SOCIAL SECURITY NUMBER / SSN", "train"),
]
UNRELATED = [
    ("the weather on the coast stayed calm for the whole of the long weekend", "fresh"),
    ("a recipe for bread that needs no kneading and one night in the fridge", "train"),
]
#: A one-word edit of a 29-word text: estimate 0.883 at seed 1714, above 0.8, and under the old
#: 8 x 16 layout the two signatures share no band, so it was never a candidate.
LONG = (
    "the applicant wrote her number on the second page of the form and then signed it before "
    "the clerk stamped every sheet in the folder with the date of arrival"
)
EDITED = "zz0" + LONG[len("the") :]
#: The estimates above are at this seed (the version's seed is the check's).
SEED = 1714


def _table(rows: list[tuple[str, str]]) -> pa.Table:
    table = with_system_columns([{"text": t, "label": "a"} for t, _ in rows])
    return table.set_column(
        table.schema.get_field_index("_dw_split"), "_dw_split", pa.array([s for _, s in rows])
    )


def _pairs(result: Any) -> list[dict[str, Any]]:
    if not result.pairs_artefact:
        return []
    return pq.read_table(resolve_under_data_dir(result.pairs_artefact)).to_pylist()


def test_every_crossing_member_of_a_near_cluster_is_paired(clean_db: None, data_dir: Path) -> None:
    """Two fresh rows near two train rows are FOUR pairs; the first-member star found two."""
    with sync_session_factory()() as db:
        v = make_version(db, _table(CLUSTER + UNRELATED), ROLES, split_roles={"fresh": True})
        result = api.check_leakage([str(v.id)], session=db)
    assert result.near_pairs == {"fresh|train": 4}
    assert result.exact_pairs == {}
    pairs = _pairs(result)
    assert {p["statistic"] for p in pairs} == {1.0}
    assert len({(p["row_key_a"], p["row_key_b"]) for p in pairs}) == 4


def test_a_repair_from_one_report_leaves_nothing_for_the_re_check(
    clean_db: None, data_dir: Path
) -> None:
    """The live loop: check, drop every reported fresh row with ``drop_leakage_pairs``, check the
    result. The re-check must find nothing (it found the cluster's next fresh member before)."""
    with sync_session_factory()() as db:
        v = make_version(db, _table(CLUSTER + UNRELATED), ROLES, split_roles={"fresh": True})
        first = api.check_leakage([str(v.id)], session=db)
        artefact = first.pairs_artefact
        assert artefact is not None
        ran = run_operator(
            "drop_leakage_pairs",
            {
                "pairs_artefact": artefact,
                "pairs_sha256": file_sha256(resolve_under_data_dir(artefact)),
                "drop_side": "fresh",
                "keep_sides": ["train"],
            },
            _table(CLUSTER + UNRELATED),
            ROLES,
            version="1.0.0",
        )
        assert ran.events.num_rows == 2  # both fresh members of the cluster
        repaired = make_version(db, ran.output, ROLES, split_roles={"fresh": True})
        again = api.check_leakage([str(repaired.id)], session=db)
    assert again.total == 0, _pairs(again)


def test_a_pair_just_above_the_threshold_is_a_candidate(clean_db: None, data_dir: Path) -> None:
    with sync_session_factory()() as db:
        v = make_version(db, _table([(LONG, "train"), (EDITED, "fresh")]), ROLES, seed=SEED)
        result = api.check_leakage([str(v.id)], session=db)
    assert result.near_pairs == {"fresh|train": 1}
    (pair,) = _pairs(result)
    assert 0.8 <= pair["statistic"] < 0.9


async def test_the_route_reports_complete_pairs_and_its_identity_says_so(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    """Through ``POST /versions/{id}/leakage`` (``dataworks_run_leakage_check``): the counts, and
    the stored report's parameters carry ``near_pairs`` so a report stored before this fix is not
    found as current (memory ``report-identity-follows-how-it-reads``)."""
    with sync_session_factory()() as db:
        v = make_version(db, _table(CLUSTER + UNRELATED), ROLES, split_roles={"fresh": True})
    response = await client.post(f"/api/v1/versions/{v.id}/leakage", json={})
    assert response.status_code == 200, response.text
    result = response.json()["report"]["result"]
    assert result["near_pairs"] == {"fresh|train": 4}
    assert result["band_layout"] == {"bands": 16, "rows": 8}
    with sync_session_factory()() as db:
        (report,) = db.query(VersionReport).filter(VersionReport.kind == "leakage").all()
        assert report.params["near_pairs"] == leakage_service.NEAR_PAIRS


def test_the_publish_and_detector_set_builder_carries_the_identity(
    clean_db: None, data_dir: Path
) -> None:
    """``api.check_leakage`` (C-3's and D-4's call) builds its parameters apart from the route."""
    with sync_session_factory()() as db:
        v = make_version(db, _table(CLUSTER), ROLES)
        result = api.check_leakage([(str(v.id), "fresh"), (str(v.id), "train")], session=db)
        report = db.get(VersionReport, result.report_id)
        assert report is not None
        assert report.params["near_pairs"] == leakage_service.NEAR_PAIRS


def test_the_recipe_step_keeps_its_first_reading() -> None:
    """``leakage_check@1.0.0`` in a recipe does not pass ``near_pairs``: a rebuilt recipe's report
    is unchanged (the first-member reading: one pair per bucket centre, the 8 x 16 layout)."""
    sink: list[dict[str, Any]] = []
    run_operator("leakage_check", {}, _table(CLUSTER), ROLES, report_sink=sink, version="1.0.0")
    assert sink[0]["band_layout"] == {"bands": 8, "rows": 16}
    assert sink[0]["near_pairs"] == {"fresh|train": 2}


def _edited(position: int, word: str) -> str:
    words = LONG.split()
    words[position] = word
    return " ".join(words)


def test_two_by_two_distinct_near_texts_are_four_pairs(clean_db: None, data_dir: Path) -> None:
    """Four DIFFERENT signatures, two per split, every crossing estimate 0.85 to 0.88: each class
    on one side is compared with each on the other, not one class with the rest."""
    rows = [
        (LONG, "train"),
        (_edited(-1, "departure"), "train"),
        (EDITED, "fresh"),
        (_edited(1, "applicants"), "fresh"),
    ]
    with sync_session_factory()() as db:
        v = make_version(db, _table(rows), ROLES, seed=SEED)
        result = api.check_leakage([str(v.id)], session=db)
    assert result.near_pairs == {"fresh|train": 4}
    assert all(0.8 <= p["statistic"] < 0.9 for p in _pairs(result))


def test_six_by_six_variants_are_thirty_six_pairs(clean_db: None, data_dir: Path) -> None:
    """Twelve signatures, six per split, many sharing each band: a bucket's classes on one side
    are each compared with every class on the other (comparing only the first gave 21 of 36)."""
    rows = [(f"{LONG} t{i}", "train") for i in range(6)] + [
        (f"{LONG} f{i}", "fresh") for i in range(6)
    ]
    with sync_session_factory()() as db:
        v = make_version(db, _table(rows), ROLES, seed=SEED)
        result = api.check_leakage([str(v.id)], session=db)
    assert result.near_pairs == {"fresh|train": 36}
