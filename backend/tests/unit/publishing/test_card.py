"""The dataset card (FR-008.7–FR-008.10, 008.63; FTASKS 7.1–7.5; AC-US3).

The fixture manifest carries the prototype's real numbers (records/format_balanced.json: 2,208 and
244, 552 and 61 per cell) and the prototype card's facts (format predicts the label 88.5%).
"""

from __future__ import annotations

import copy

import yaml

from src.services.publishing import card
from src.services.publishing.checks import CheckOutcome, Outcome
from tests.support.manifest_fixtures import rows_manifest

DIGESTS = [{"path": "data/train.parquet", "bytes": 181234, "sha256": "a" * 64}]
WARNING = CheckOutcome(
    "C-7",
    Outcome.AMBER,
    "Column 'source_label' predicts the label at or above its warning level.",
    "Balance the version on that column or remove it.",
    {"warnings": [{"column": "source_label", "figure": 0.885}], "invalid": []},
)


def test_front_matter_round_trips_and_maps_the_splits() -> None:
    fm = card.front_matter(rows_manifest())
    assert fm["license"] == "cc-by-2.0"
    assert fm["task_categories"] == ["text-classification"]
    assert fm["configs"] == [
        {
            "config_name": "default",
            "data_files": [
                {"split": "train", "path": "data/train.parquet"},
                {"split": "test", "path": "data/test.parquet"},
            ],
        }
    ]
    assert yaml.safe_load(yaml.safe_dump(fm)) == fm


def test_mixed_or_unstated_licences_make_the_front_matter_other() -> None:
    doc = rows_manifest()
    second = copy.deepcopy(doc["sources"][0])
    second["source_id"] = "src-offensive"
    second["licence"].update(
        displayed="not stated", raw=None, origin="none", licence_class="private_only"
    )
    doc["sources"].append(second)
    assert card.front_matter(doc)["license"] == "other"
    doc["sources"] = [doc["sources"][0], dict(doc["sources"][0], source_id="x")]
    doc["sources"][1] = copy.deepcopy(doc["sources"][1])
    doc["sources"][1]["licence"]["raw"] = "mit"
    assert card.front_matter(doc)["license"] == "other", "two different permitted ids"


def test_the_record_lists_every_fact() -> None:
    doc = rows_manifest()
    doc["caveats"] = card.caveats_from_outcomes([WARNING])
    record = card.record_section(
        doc,
        [WARNING],
        drop_summary=[
            {
                "step_index": 1,
                "operator": "threshold_labeler",
                "operator_version": "1",
                "dropped": 13195,
                "rows_out": 14086,
            },
        ],
        digests=DIGESTS,
        history=[{"version": "v1", "commit": card.THIS_COMMIT, "date": "2026-10-07"}],
    )
    for fact in (
        "CreativeLang/ColBERT_Humor_Detection",
        "2bb7d6bc",
        "cc-by-2.0",
        "a" * 64,
        "seed 20261005",
        "dw.rowkey/v1",
        "threshold_labeler",
        "13,195",
        "| train | 2,208 | humorous: 1,104, not_humorous: 1,104 | no |",
        "| test | 244 | humorous: 122, not_humorous: 122 | yes |",
        "source_label",
        "shortcut_warning",
        "| `data/train.parquet` | 181,234 |",
        card.HISTORY_HEADING,
    ):
        assert fact in record, fact
    assert record.startswith(card.RECORD_START) and record.endswith(card.RECORD_END)


def _assembled(prose: str) -> str:
    doc = rows_manifest()
    doc["caveats"] = card.caveats_from_outcomes([WARNING])
    record = card.record_section(doc, [WARNING], drop_summary=[], digests=DIGESTS, history=[])
    return card.assemble(card.front_matter(doc), prose, record).decode()


def test_an_edit_cannot_replace_the_front_matter_or_delete_a_caveat() -> None:
    """AC-US3: prose edits survive; front matter and record are regenerated."""
    hostile = (
        "---\nlicense: mit\nconfigs: []\n---\n# My title\n\nMy words.\n\n"
        + card.RECORD_START
        + "\n## Record\nNo caveats at all.\n"
        + card.RECORD_END
        + "\n\nMore of my words."
    )
    text = _assembled(hostile)
    fm = card.read_front_matter(text)
    assert fm["license"] == "cc-by-2.0" and fm["configs"][0]["data_files"]
    assert "# My title" in text and "My words." in text and "More of my words." in text
    assert "No caveats at all." not in text
    assert "source_label" in text and text.count(card.RECORD_START) == 1


def test_history_is_merged_by_version_and_never_shortened() -> None:
    first = card.merge_history(
        [], {"version": "v1", "commit": card.THIS_COMMIT, "date": "2026-10-01"}, {}
    )
    readme = (
        "x\n"
        + card.HISTORY_HEADING
        + "\n\n"
        + "\n".join(card._history_rows(first))
        + "\n\n"
        + card.RECORD_END
    )
    parsed = card.parse_history(readme)
    assert parsed == first
    second = card.merge_history(
        parsed,
        {"version": "v2", "commit": card.THIS_COMMIT, "date": "2026-10-07"},
        {"v1": "c" * 40},
    )
    assert [r["version"] for r in second] == ["v1", "v2"]
    assert second[0]["commit"] == "c" * 40 and second[1]["commit"] == card.THIS_COMMIT
    third = card.merge_history(
        second,
        {"version": "v2", "commit": card.THIS_COMMIT, "date": "2026-10-09"},
        {"v1": "c" * 40},
    )
    assert third == second, "republishing a version keeps its first row"


def test_a_forbids_source_carries_the_evaluation_only_notice() -> None:
    doc = rows_manifest()
    doc["sources"][0]["licence"]["licence_class"] = "forbids_redistribution"
    record = card.record_section(doc, [], drop_summary=[], digests=DIGESTS, history=[])
    assert card.EVALUATION_ONLY in record
    assert card.front_matter(doc)["license"] == "other"


def test_a_calibration_on_a_private_set_is_cited_not_shipped() -> None:
    doc = rows_manifest()
    doc["content"]["calibration"] = [
        {
            "labeler_fingerprint": "f" * 64,
            "status": "recorded",
            "record_id": "cal_1",
            "verdict": "passes",
            "rule": "default_c3",
            "auroc": {"value": 0.887, "ci_low": 0.86, "ci_high": 0.91, "n": 2000},
            "calibration_set": {
                "id": "cs_humicroedit",
                "licence_class": "private_only",
                "rows_shipped": False,
            },
        }
    ]
    record = card.record_section(doc, [], drop_summary=[], digests=DIGESTS, history=[])
    assert "cs_humicroedit is cited, its rows are not shipped" in record
    assert "AUROC 0.887" in record


def test_caveats_from_outcomes_keep_amber_and_notes() -> None:
    note = CheckOutcome("N-unpinned_labeler", Outcome.NOTE, "unpinned", "n", {"fingerprint": "f"})
    green = CheckOutcome("C-1", Outcome.GREEN, "ok", "n")
    caveats = card.caveats_from_outcomes([WARNING, note, green])
    assert [(c["code"], c["severity"]) for c in caveats] == [
        ("shortcut_warning", "amber"),
        ("unpinned_labeler", "note"),
    ]
