"""Where the brand guard enforces, and that the tree is ready for it (feature 020; FR-020.23;
suite decision R23, operator, 2026-11-11; FTASKS 17.1, 17.2).

R23: from P4's close the guard is ENFORCED in the private repository's CI and runs in REPORT mode
on the public mirror. The mirror carries only the public export, which withholds the old host
names (DR-29) and the private organisation's repository names, so the same tree would show hits
there that are allowed here; the private CI is the gate, because a commit reaches the mirror only
after passing it. ``brand-guard.json`` still holds the one mode and still follows the export's
``closed`` (``test_brand_phase_rules``); what changes on the mirror is the mode a scanner APPLIES.

The dry run is the last P4 push's proof (FR-020.23): every scanner, with the mode forced to
enforce and the export read as closed at P4, on the committed tree.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.support import git_snapshots
from tests.support import legacy_identifiers as li
from tests.support.source_tree import mirror_snapshot
from tests.unit.brand import scan

DENY = scan.policy()["deny"]


@pytest.mark.parametrize("shape", sorted(git_snapshots.EXPECTED))
def test_enforce_applies_in_the_private_tree_and_report_on_the_mirror(
    tmp_path: Path, shape: str
) -> None:
    tree = git_snapshots.make_repo(tmp_path / "t", shape)
    expected = "report" if git_snapshots.EXPECTED[shape] else "enforce"
    assert scan.applied_mode("enforce", root=tree) == expected, shape
    assert scan.applied_mode("report", root=tree) == "report", shape


def test_a_hit_fails_the_private_tree_and_is_only_recorded_on_the_mirror(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same hits, the same policy mode: red here, a printed count there."""
    monkeypatch.setattr(scan, "REPORT", tmp_path / "report.json")
    private = git_snapshots.make_repo(tmp_path / "private", "private")
    mirror = git_snapshots.make_repo(tmp_path / "mirror", "mirror")
    hits = ["k8s/base/config.yaml: millm"]
    with pytest.raises(AssertionError, match="1 unallowed old name"):
        scan.judge("manifests", hits, mode="enforce", root=private)
    assert scan.judge("manifests", hits, mode="enforce", root=mirror) == 1
    out = capsys.readouterr().out
    assert "brand-guard[manifests] k8s/base/config.yaml: millm" in out
    assert "report mode on the public mirror (R23)" in out
    assert json.loads((tmp_path / "report.json").read_text())["manifests"] == 1
    assert scan.judge("manifests", [], mode="enforce", root=private) == 0


def test_this_tree_applies_the_committed_mode_unless_it_is_the_mirror() -> None:
    """The wiring: every scanner's verdict goes through ``judge``, and ``judge`` with no
    arguments reads the committed policy and THIS tree (private CI: the policy's own mode).
    """
    expected = "report" if mirror_snapshot() else scan.policy()["mode"]
    assert scan.applied_mode() == expected


def closed_at_p4() -> li.Export:
    """The committed export, read as closed at P4 at the earliest (S001's P4-close regeneration).

    A later committed phase is KEPT, never wound back: from P5 the tree is written to P5's rules
    (``AGENT.md`` names the suite by ``~/app/miStudio/...`` paths, allowed by ``G-STUDIO-PATH``
    only from P5; FTASKS 18.3), so reading a P5 export as P4 would deny what P5 allows."""
    export = copy.deepcopy(li.load())
    for key in ("phase", "closed"):
        if not li.at_least(export.header[key], "P4"):
            export.header[key] = "P4"
    assert li.enforce_mode_expected(export)
    return export


#: Hits the enforce dry run still finds, each with what clears it. NOT an allowance: the scanners
#: report these on every run, and the private tree is red on any hit that is not listed here (the
#: first test below is also red when a listed hit disappears).
#:
#: EMPTY since the P4 close. What waited here and how each was cleared:
#: - manifests (4): the previous-address lists name the siblings' old in-cluster services until P7
#:   (FR-020.14). The register's P4 rows give this repository the two ``host`` records
#:   (``OLD_IN_CLUSTER``), in transition, and the loaders honour a ``host`` record in transition.
#: - backend_literals (2): suite decision R25 (operator, 2026-11-11) changed the ``feature_filter@1``
#:   manifest description (with its golden hash line) and the ``behaviour`` description of the
#:   published contract ``midataworks.rubric/v1`` (regenerated; miWorkbench re-vendors it).
WAITS_ON_THE_REGISTER: dict[str, list[str]] = {
    "backend_literals": [],
    "manifests": [],
}
OLD_IN_CLUSTER = (
    "millm-backend.millm.svc.cluster.local",
    "mistudio-backend.mistudio.svc.cluster.local",
)
FROZEN_TEXT = sorted(WAITS_ON_THE_REGISTER["backend_literals"])


def _no_line(hit: str) -> str:
    """``path:line: hit`` -> ``path: hit`` (a line number is not part of what is waited on)."""
    where, _, found = hit.rpartition(": ")
    path, _, line = where.rpartition(":")
    return f"{path}: {found}" if line.isdigit() else hit


def _surfaces(export: li.Export) -> dict[str, list[str]]:
    if mirror_snapshot():
        pytest.skip(
            "MIRROR: the enforce dry run is the private repository's proof (R23); the public "
            "export withholds DR-29 and the private repository names"
        )
    return {
        "backend_literals": scan.backend_hits(export, DENY)[0],
        "mcp": scan.mcp_hits(export, DENY)[0],
        "documents": scan.document_hits(export, DENY)[0],
        "manifests": scan.manifest_hits(export, DENY),
    }


def test_enforce_dry_run_at_p4_close_finds_only_what_waits_on_the_register() -> None:
    found = _surfaces(closed_at_p4())
    assert set(found) == {"backend_literals", "mcp", "documents", "manifests"}
    for scanner, hits in found.items():
        waited = sorted(WAITS_ON_THE_REGISTER.get(scanner, []))
        assert sorted(_no_line(h) for h in hits) == waited, (scanner, hits[:20])


def test_the_two_host_records_are_what_clears_the_manifests() -> None:
    """The wiring of the zero above: take the two ``host`` records out of the export and the
    manifests show the four old in-cluster names again, and nothing else changes. So the dry run is
    clean BECAUSE the register lists those hosts, not because the scanner stopped reading them.
    """
    export = closed_at_p4()
    assert _surfaces(export)["manifests"] == []  # skips on the mirror, like every dry run here
    listed = [r for r in export.section("host") if r["old"] in OLD_IN_CLUSTER]
    assert sorted(r["old"] for r in listed) == sorted(OLD_IN_CLUSTER), listed
    assert {r["status"] for r in listed} == {"transition"}
    export.records = [r for r in export.records if r not in listed]
    found = _surfaces(export)
    assert sorted(_no_line(h) for h in found.pop("manifests")) == [
        "k8s/base/config.yaml: millm",
        "k8s/base/config.yaml: millm",
        "k8s/base/config.yaml: mistudio",
        "k8s/base/config.yaml: mistudio",
    ]
    assert sorted(_no_line(h) for h in found.pop("backend_literals")) == FROZEN_TEXT
    for scanner, hits in found.items():
        assert hits == [], (scanner, hits[:20])


def test_the_dry_run_reads_every_surface_it_names() -> None:
    """Fail closed: a scanner that listed nothing would be clean for the wrong reason."""
    assert len(scan.backend_files()) > 100
    assert len([w for w, _ in scan.mcp_texts() if w.startswith("tool ")]) > 200
    assert {w for w, _ in scan.documents()} >= {"README.md", ".env.example"}
    assert len(scan.manifests()) > 200


def test_a_planted_old_name_is_found_on_each_surface_under_the_p4_export() -> None:
    """The dry run is not green because nothing can be red: one plant per surface class."""
    export = closed_at_p4()
    assert li.classified_hits("Load the model in miLLM first.", export, DENY)[0] == ["miLLM"]
    assert li.classified_hits("Open miStudio", export, DENY, whole=False)[0] == ["miStudio"]
    assert li.classified_hits("MILLM_FOO", export, DENY, whole=False, code_spans=False)[0] == [
        "MILLM"
    ]
