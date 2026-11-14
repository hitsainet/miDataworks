"""Text-only frozen entries (feature 020; FR-020.3): DR-27, DR-28, DR-29, DR-31.

Nothing executes these names; a presence check is the proof, and for DR-29 and DR-31 the proof is
that the brand guard ALLOWS them exactly while their register record says so."""

from __future__ import annotations

import copy

import pytest
import yaml

from tests.support import legacy_identifiers as li
from tests.support.frozen import exercises

REPO = li.REPO
CAPTURES = REPO / "backend" / "tests" / "fixtures" / "labeling" / "millm"
DENY = "(?i)mi(studio|llm)"


@exercises("DR-27", literals=("hitsai/millm-backend",))
def test_dr27_captured_responses_keep_the_old_image_repo(literal: str) -> None:
    """The captured miLLM responses are recordings: they keep the image name they were made with."""
    files = sorted(CAPTURES.rglob("*"))
    assert files, f"no captured responses under {CAPTURES}"
    assert any(literal in p.read_text(errors="ignore") for p in files if p.is_file())


@exercises("DR-28")
def test_dr28_backup_mount_is_not_moved() -> None:
    """The nightly backup writes to the separate disk's existing folder; it is not renamed."""
    docs = list(yaml.safe_load_all((REPO / "k8s" / "base" / "db-backup.yaml").read_text()))
    (cron,) = [d for d in docs if d and d["kind"] == "CronJob"]
    volumes = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
    paths = [v["hostPath"]["path"] for v in volumes if "hostPath" in v]
    assert paths == ["/mnt/hdd2/pv/midataworks"]


def _with_status(export: li.Export, entry_id: str, status: str) -> li.Export:
    changed = copy.deepcopy(export)
    for record in changed.records:
        if record.get("id") == entry_id:
            record["status"] = status
    return changed


@exercises(
    "DR-29",
    literals=(
        "k8s-mistudio.hitsai.local",
        "k8s-millm.hitsai.local",
        "millm.hitsai.net",
        "mcp-mistudio.hitsai.net",
        "hitsainet.github.io/miStudio",
    ),
)
def test_dr29_old_hosts_allowed_until_retired(literal: str) -> None:
    export = li.load()
    record = li.require_entry(export, "DR-29")
    if literal not in record["literals"]:
        pytest.skip(f"{literal} is not one of this repository's DR-29 literals")
    line = f"reach it at {literal} for now"
    assert li.unallowed_hits(line, _with_status(export, "DR-29", "scheduled"), DENY) == []
    assert li.unallowed_hits(line, _with_status(export, "DR-29", "retired"), DENY) != []


@exercises("DR-31")
def test_dr31_document_ids_are_allowed_as_records() -> None:
    """Document ids are history (ID-6): one planted in a read surface is allowed by DR-31."""
    export = li.load()
    li.require_entry(export, "DR-31")
    line = "Specified in BRD-MIS-DATAWORKS-001 and 000_PADR|miStudio."
    assert li.unallowed_hits(line, export, DENY) == []
    without = copy.deepcopy(export)
    without.records = [r for r in without.records if r.get("id") != "DR-31"]
    assert li.unallowed_hits(line, without, DENY) != [], "the allowance comes from DR-31"
