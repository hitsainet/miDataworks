"""The audit over stored versions: label resolution, cross-tab, refusals, warnings (FTASKS 4.3–4.6,
5.2, 5.3) on real PostgreSQL and Parquet files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from src.core.database import sync_session_factory
from src.models import VersionReport
from src.services.curation import api, audit_service, label_columns, level_service
from src.services.curation.audit_service import AuditRefusal
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import (
    StepFixture,
    StubRegistry,
    labeler_info,
    make_version,
)

LABELER = labeler_info("threshold_labeler", {"label": "metadata", "label_probability": "metadata"})


@pytest.fixture
def stub_labeler(monkeypatch: pytest.MonkeyPatch) -> StubRegistry:
    reg = StubRegistry({"threshold_labeler": LABELER})
    monkeypatch.setattr(label_columns, "_registry", lambda: reg)
    return reg


def _small(n: int = 60) -> pa.Table:
    rows = [
        {
            "text": f"row {i} " + "w" * (i % 7),
            "source_label": "joke" if i % 2 else "news",
            "format": "joke" if i % 2 else "headline",
            "id": f"id{i}",
            "label": "humorous" if i % 2 else "not_humorous",
            "label_probability": 0.9 if i % 2 else 0.1,
        }
        for i in range(n)
    ]
    return hp.with_system_columns(rows)


class TestLabelResolution:
    def test_newest_labeler_provides_label_and_derived(
        self, clean_db: None, data_dir: Path, stub_labeler: StubRegistry
    ) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, _small(), hp.ROLES, steps=[StepFixture("threshold_labeler")])
            labels = label_columns.resolve(db, v.id)
        assert labels.label == "label" and labels.source == "labeler"
        assert labels.derived == {"label_probability": "threshold_labeler@1"}

    def test_no_labeler_and_no_choice_is_no_label(
        self, clean_db: None, data_dir: Path, stub_labeler: StubRegistry
    ) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, _small(), hp.ROLES)
            assert label_columns.resolve(db, v.id).label is None
            chosen = label_columns.resolve(db, v.id, "source_label")
        assert chosen.label == "source_label" and chosen.source == "chosen"

    def test_label_is_never_inferred_from_a_column_name(
        self, clean_db: None, data_dir: Path, stub_labeler: StubRegistry
    ) -> None:
        # The version HAS a column named "label", but no labeler wrote it: no label.
        with sync_session_factory()() as db:
            v = make_version(db, _small(), hp.ROLES, steps=[StepFixture("some_filter")])
            assert label_columns.resolve(db, v.id).label is None


def _audit(version_id: str, label: str | None = None, **kw: Any) -> dict[str, Any]:
    params = api.audit_params(label)
    with sync_session_factory()() as db:
        result, _ = audit_service.run_audit(
            db, [api.ReportInput(version_id)], params, kw.get("seed", 7)
        )
    return result


class TestRefusals:
    def test_no_label_column(self, clean_db: None, data_dir: Path, stub_labeler: Any) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, _small(), hp.ROLES)
        with pytest.raises(AuditRefusal) as exc:
            _audit(v.id)
        assert exc.value.code == "no_label_column"

    def test_one_class(self, clean_db: None, data_dir: Path, stub_labeler: Any) -> None:
        table = _small()
        table = table.set_column(
            table.schema.get_field_index("label"), "label", pa.array(["x"] * table.num_rows)
        )
        with sync_session_factory()() as db:
            v = make_version(db, table, hp.ROLES)
        with pytest.raises(AuditRefusal) as exc:
            _audit(v.id, "label")
        assert exc.value.code == "single_class_label"

    def test_too_few_rows(self, clean_db: None, data_dir: Path, stub_labeler: Any) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, _small(8), hp.ROLES)
        with pytest.raises(AuditRefusal) as exc:
            _audit(v.id, "label")
        assert exc.value.code == "insufficient_rows"
        assert exc.value.details["minimum_per_class"] == 5


class TestCrossTab:
    def test_excluded_by_band_counts_come_from_the_labelers_input(
        self, clean_db: None, data_dir: Path, stub_labeler: Any
    ) -> None:
        kept = _small(40)
        excluded_rows = hp.with_system_columns(
            [
                {
                    "text": f"excluded {i}",
                    "source_label": "news",
                    "format": "headline",
                    "id": f"x{i}",
                    "label": None,
                    "label_probability": 0.3,
                }
                for i in range(6)
            ]
        )
        before = pa.concat_tables([kept, excluded_rows], promote_options="default")
        events = [
            {
                "kind": "dropped",
                "row_key": r["_dw_row_key"],
                "occurrence": r["_dw_occurrence"],
                "reason_code": "excluded_by_band",
                "reason": "P in the exclusion band",
                "statistic_name": "P",
                "statistic_value": 0.3,
            }
            for r in excluded_rows.to_pylist()
        ]
        with sync_session_factory()() as db:
            v = make_version(
                db,
                kept,
                hp.ROLES,
                steps=[
                    StepFixture("assemble_like", output=before),
                    StepFixture("threshold_labeler", output=kept, events=events),
                ],
            )
        result = _audit(v.id)
        assert result["excluded_by_band"]["source_label"] == {"news": 6}
        assert result["excluded_by_band"]["format"] == {"headline": 6}
        by = {c["column"]: c for c in result["columns"]}
        values = {item["value"]: item for item in by["source_label"]["per_value"]["values"]}
        assert values["joke"]["counts_by_label"] == {"humorous": 20}

    def test_nulls_are_a_cell(self, clean_db: None, data_dir: Path, stub_labeler: Any) -> None:
        table = _small(60)
        fmt = table.column("format").to_pylist()
        fmt[0] = None
        table = table.set_column(table.schema.get_field_index("format"), "format", pa.array(fmt))
        with sync_session_factory()() as db:
            v = make_version(db, table, hp.ROLES)
        by = {c["column"]: c for c in _audit(v.id, "label")["columns"]}
        assert "(empty)" in {item["value"] for item in by["format"]["per_value"]["values"]}


class TestWarnings:
    def test_level_change_after_audit_changes_warning_not_figures(
        self, clean_db: None, data_dir: Path, stub_labeler: Any
    ) -> None:
        with sync_session_factory()() as db:
            v = make_version(
                db, hp.candidates(), hp.ROLES, steps=[StepFixture("threshold_labeler")]
            )
            dataset_id = v.dataset_id
            first = api.evaluate_warnings([(v.id, None)], session=db)
        assert first.label_column == "label"
        assert {w.column for w in first.warnings} == {"source_label", "format"}
        assert first.level.source == "code_default" and first.level.margin_pp == 10
        assert first.warnings[0].level_source == "code_default"
        with sync_session_factory()() as db:
            level_service.set_level(
                db, dataset_id=dataset_id, margin_pp=40, reason="format is the target", set_by="op"
            )
            second = api.evaluate_warnings([(v.id, None)], session=db)
        assert second.warnings == [] and second.level.source == "dataset"
        assert second.report_id == first.report_id  # the stored figures were reused
        with sync_session_factory()() as db:
            assert db.query(VersionReport).count() == 1
            level_service.clear_level(db, dataset_id=dataset_id, reason="back", set_by="op")
            third = api.evaluate_warnings([(v.id, None)], session=db)
        assert {w.column for w in third.warnings} == {"source_label", "format"}

    def test_no_label_reports_status_not_a_pass(
        self, clean_db: None, data_dir: Path, stub_labeler: Any
    ) -> None:
        with sync_session_factory()() as db:
            v = make_version(db, _small(), hp.ROLES)
            out = api.evaluate_warnings([v.id], session=db)
        assert out.status == "no_label_column" and out.report_id is None


class TestLevels:
    def test_resolution_global_override_clear(self, clean_db: None) -> None:
        from tests.support import db_factories as f

        with sync_session_factory()() as db:
            ds = f.dataset(db)
            db.commit()
            assert level_service.effective_level(db, ds.id).margin_pp == 10
            level_service.set_level(db, dataset_id=None, margin_pp=12, reason="g", set_by="op")
            assert level_service.effective_level(db, ds.id).source == "global"
            level_service.set_level(db, dataset_id=ds.id, margin_pp=30, reason="o", set_by="op")
            eff = level_service.effective_level(db, ds.id)
            assert (eff.margin_pp, eff.source, eff.set_by) == (30, "dataset", "op")
            level_service.clear_level(db, dataset_id=ds.id, reason="c", set_by="op")
            assert level_service.effective_level(db, ds.id).margin_pp == 12
            assert [h["action"] for h in level_service.history(db, ds.id)] == ["clear", "set"]


def test_service_writes_the_callers_origin_so_the_table_refuses_an_agent(clean_db: None) -> None:
    """If a route ever lost its 403, the table check must still refuse (P-09's second wall)."""
    from sqlalchemy.exc import IntegrityError

    with sync_session_factory()() as db:
        with pytest.raises(IntegrityError, match="origin_operator_only"):
            level_service.set_level(
                db, dataset_id=None, margin_pp=50, reason="r", set_by="agent:x", origin="agent"
            )


class TestTrlCheckMode:
    def _version(self, rejected: list[str]) -> str:
        rows = [{"prompt": "p", "chosen": f"c{i}", "rejected": r} for i, r in enumerate(rejected)]
        table = hp.with_system_columns(rows, content=("prompt", "chosen", "rejected"))
        with sync_session_factory()() as db:
            v = make_version(
                db, table, {"prompt": "content", "chosen": "content", "rejected": "content"}
            )
            return str(v.id)

    def test_dpo_with_an_empty_rejected_is_invalid_naming_rule_and_column(
        self, clean_db: None, data_dir: Path
    ) -> None:
        vid = self._version(["fine", ""])
        with sync_session_factory()() as db:
            result = api.validate_trl(vid, "dpo", session=db)
        assert result.valid is False and result.trl_version == "1.14.1"
        assert result.failures[0]["rule"] == "non_empty:rejected"
        assert result.failures[0]["column"] == "rejected"
        assert result.not_checked == [
            {"rule": "context_window", "reason": result.not_checked[0]["reason"]}
        ]

    def test_valid_version_and_missing_columns(self, clean_db: None, data_dir: Path) -> None:
        vid = self._version(["a", "b"])
        with sync_session_factory()() as db:
            assert api.validate_trl(vid, "dpo", session=db).valid is True
            kto = api.validate_trl(vid, "kto", session=db)
        assert kto.valid is False and kto.failures[0]["rule"] == "required_columns"
