"""The card says when sources reached the version through a rule set only (016 FR-016.31)."""

from __future__ import annotations

from src.services.publishing import card
from tests.support.manifest_fixtures import rows_manifest

DIGESTS = [{"path": "data/train.parquet", "bytes": 181234, "sha256": "a" * 64}]


def test_the_record_section_says_the_sources_taught_rules() -> None:
    doc = rows_manifest()
    lineage = dict((doc.get("extensions") or {}).get("lineage") or {})
    lineage["rule_sources"] = [{"version_id": "v-src", "number": 3}]
    doc["extensions"] = {**(doc.get("extensions") or {}), "lineage": lineage}
    record = card.record_section(doc, [], drop_summary=[], digests=DIGESTS, history=[])
    assert "derived from version v3 (`v-src`)" in record
    assert "their labels taught its rules" in record


def test_without_rule_sources_the_record_says_nothing_of_them() -> None:
    record = card.record_section(rows_manifest(), [], drop_summary=[], digests=DIGESTS, history=[])
    assert "taught its rules" not in record
