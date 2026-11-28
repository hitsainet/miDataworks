"""The previous-address lookup reads one index entry per DISTINCT address, not one per row
(feature 020; FTASKS 6.15 follow-up, 2026-11-08).

``matching_values`` once ran ``SELECT DISTINCT <address>``, which reads every row: 12-15 ms p50 at
100,000 rows (budget 5 ms). It now runs a loose index scan (a recursive CTE stepping through the
index that leads with the address column). Timing-independent proof: ``EXPLAIN (ANALYZE)`` of the
statement the code builds, on a table seeded with many rows and few addresses; the rows the plan
reads from the table must be of the order of the distinct count, never the table size (control:
revert to ``SELECT DISTINCT`` and this is red).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from src.core import sibling_origins as so
from src.core.database import get_sync_engine, sync_session_factory
from src.models.detector_results import RewardMark
from src.models.detector_send import MiStudioRegistration
from src.models.reproduction_link import ReproductionLink
from tests.support import db_factories

ROWS = 4000
HOSTS = [f"http://h{i}.test:8000" for i in range(6)]


def _seed(table: str) -> None:
    params: dict[str, Any] = {"h": HOSTS, "n": len(HOSTS), "c": ROWS}
    with sync_session_factory()() as db:
        version_id = str(uuid.uuid4())
        db_factories.version(db, id=version_id)
        db.commit()
    params["v"] = version_id
    sql = {
        "dw_reward_marks": (
            "INSERT INTO dw_reward_marks (id, mistudio_base_url, mistudio_probe_id, source, reason,"
            " marked_by, marked_by_origin) SELECT 'rwm_' || g, (:h)[1 + g % :n], 'pm_' || g,"
            " 'operator', 'r', 'r', 'operator' FROM generate_series(1, :c) g"
        ),
        "dw_mistudio_registrations": (
            "INSERT INTO dw_mistudio_registrations (id, kind, mistudio_base_url, split,"
            " mistudio_dataset_id, role, mapping_sha256, columns_sha256, probe_dataset_id,"
            " version_id) SELECT 'msr_' || g, 'view', (:h)[1 + g % :n], 'test', 'ds_' || g,"
            " 'train', repeat('0', 64), repeat('1', 64), 'pmd_' || g, CAST(:v AS uuid)"
            " FROM generate_series(1, :c) g"
        ),
        "dw_reproduction_links": (
            "INSERT INTO dw_reproduction_links (id, mistudio_base_url, mistudio_probe_id,"
            " probe_dataset_id, role, version_id, split, input_column, label_column, label_mapping,"
            " auroc, ci_low, ci_high, n_positive, n_negative, check_level, checks, scoring_form,"
            " evidence, created_by, created_by_origin) SELECT 'rpl_' || g, (:h)[1 + g % :n],"
            " 'pm_' || g, 'pmd', 'id_test', CAST(:v AS uuid), 'test', 'text', 'label', '{}',"
            " 0.9, 0.8, 0.95, 1, 1, 'counts_only', '{}', '{}', '{}', 'r', 'operator'"
            " FROM generate_series(1, :c) g"
        ),
    }[table]
    with get_sync_engine().begin() as conn:
        conn.execute(text(sql), params)
        conn.execute(text(f"ANALYZE {table}"))


def _rows_read(plan: Any, table: str) -> int:
    """Rows produced by every scan node on ``table`` (actual rows x loops)."""
    total = 0
    if isinstance(plan, dict):
        if plan.get("Relation Name") == table:
            total += int(plan.get("Actual Rows", 0)) * int(plan.get("Actual Loops", 1))
        for child in plan.get("Plans", []):
            total += _rows_read(child, table)
    return total


@pytest.mark.parametrize(
    ("column", "table"),
    [
        (RewardMark.mistudio_base_url, "dw_reward_marks"),
        (MiStudioRegistration.mistudio_base_url, "dw_mistudio_registrations"),
        (ReproductionLink.mistudio_base_url, "dw_reproduction_links"),
    ],
)
def test_the_lookup_reads_one_entry_per_address_not_per_row(
    clean_db: None, column: Any, table: str
) -> None:
    _seed(table)
    statement = so.distinct_values_statement(column)
    with sync_session_factory()() as db:
        assert sorted(db.scalars(statement)) == sorted(HOSTS), "every distinct address, once"
        compiled = statement.compile(db.get_bind(), compile_kwargs={"literal_binds": True})
        [[plan]] = db.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}")).all()
    read = _rows_read(plan[0]["Plan"], table)
    assert read <= 4 * (len(HOSTS) + 1), f"{read} rows read from {table} for {len(HOSTS)} addresses"


def test_matching_values_runs_the_loose_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring: the lookup executes the statement the plan test measured."""
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "mistudio_base_url", HOSTS[0])
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", (HOSTS[1],))
    seen: list[str] = []

    class Session:
        def scalars(self, statement: Any) -> list[str]:
            seen.append(str(statement))
            return [HOSTS[1]]

    assert so.matching_values(
        Session(), RewardMark.mistudio_base_url, so.Sibling.MIWORKBENCH, HOSTS[0]
    ) == sorted(HOSTS[:2])
    expected = str(so.distinct_values_statement(RewardMark.mistudio_base_url))
    assert seen == [expected]
    assert "RECURSIVE" in expected.upper()
