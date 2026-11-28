"""Every copied file names its origin, and docs/REUSE.md lists it (ADR-001, R-03.61; task 2.5).

A file whose header says it came from a sibling must carry the repository, the path and the
commit, and must have a row in ``docs/REUSE.md``; every row must point at a file that exists.

Since the rebrand's P4 (feature 020, FR-020.20) the header and the row name the origin by its NEW
product and repository name: ``miWorkbench (Onegaishimas/miWorkbench)``, ``miServer
(Onegaishimas/miServer)``. The commit cited is unchanged; it resolves in the renamed repository.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.support import legacy_identifiers as li
from tests.support.source_tree import source_only

REPO = Path(__file__).resolve().parents[3]
SCANNED = [REPO / "backend" / "src", REPO / "backend" / "tests", REPO / "frontend" / "src"]
HEADER = re.compile(r"Origin[^:]*:\s*(miWorkbench|miServer|miForge)\s*\(([^)]+)\)\s*(\S+)")
COMMIT = re.compile(r"@\s*([0-9a-f]{7,40})")
ROW = re.compile(
    r"^\|\s*(miWorkbench|miServer|miForge)\s*\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{7,40})`\s*\|\s*`([^`]+)`\s*\|\s*(copy|adapt)",
    re.M,
)


def copied_files() -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for root in SCANNED:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            head = "\n".join(path.read_text(errors="ignore").splitlines()[:6])
            match = HEADER.search(head)
            if match:
                commit = COMMIT.search(head)
                assert commit, f"{path.relative_to(REPO)} names an origin but no commit"
                found[str(path.relative_to(REPO))] = (
                    match.group(1),
                    match.group(3),
                    commit.group(1),
                )
    return found


def register() -> dict[str, tuple[str, str, str]]:
    # docs/REUSE.md is stripped from the public mirror: skip loudly there, fail if missing here.
    text = source_only("docs/REUSE.md").read_text()
    return {m.group(4): (m.group(1), m.group(2), m.group(3)) for m in ROW.finditer(text)}


def test_every_copied_file_is_registered() -> None:
    """Both ways (control M15): a header pattern that stops matching, for instance one that does
    not know the origins' new names, finds no copied file; with only ``files - rows`` checked that
    reads as "nothing is missing"."""
    rows = register()
    files = copied_files()
    missing = sorted(set(files) - set(rows))
    assert not missing, f"copied files with no docs/REUSE.md row: {missing}"
    unseen = sorted(set(rows) - set(files))
    assert not unseen, f"docs/REUSE.md rows whose file's origin header was not read: {unseen}"


def test_every_row_points_at_an_existing_file_with_a_matching_header() -> None:
    files = copied_files()
    for destination, (repo, origin, commit) in register().items():
        assert (REPO / destination).exists(), f"REUSE.md lists {destination}, which does not exist"
        assert destination in files, f"{destination} has no origin header"
        assert files[destination] == (repo, origin, commit), destination


def test_the_scan_finds_copied_code() -> None:
    assert len(copied_files()) >= 10


# --- feature 020 (FR-020.20; CF-13): miForge's copies, and old repository names in headers ------

#: The number of copied files at P0 (020 FTASKS 7.7). The P4 header rewrite must keep it (15.6).
#: Copied files at P0 (020 FTASKS 7.7): the P4 header rewrite must keep this count (15.6).
EXPECTED_COPIED = 63
SLUG = re.compile(r"\((Onegaishimas/[A-Za-z]+)\)")


def test_miforge_origins_are_registered() -> None:
    """Latent defect fixed in P0: two files copied from miForge named their origin, but the header
    and row patterns knew only miStudio and miLLM, so neither was checked or listed."""
    files = copied_files()
    rows = register()
    from_miforge = {d for d, (repo, _, _) in files.items() if repo == "miForge"}
    assert from_miforge == {
        "backend/src/core/runtime_settings.py",
        "frontend/src/components/settings/EnvironmentCard.tsx",
    }
    for destination in from_miforge:
        assert rows[destination][0] == "miForge", destination


def test_copied_file_count_is_pinned() -> None:
    assert len(copied_files()) == len(register()) == EXPECTED_COPIED


def old_slug_violations(export: li.Export, roots: list[Path] | None = None) -> list[str]:
    """Headers naming an old repository slug that the export does not list as in transition."""
    allowed = {r["old"] for r in export.section("repo") if r["status"] == "transition"}
    old = {r["old"] for r in export.section("repo")}
    found = []
    for root in SCANNED if roots is None else roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            head = "\n".join(path.read_text(errors="ignore").splitlines()[:6])
            if not ANY_ORIGIN.search(head):
                continue
            for slug in SLUG.findall(head):
                if slug in old and slug not in allowed:
                    found.append(f"{path.name}: {slug}")
    return found


def test_old_repo_slug_allowed_only_while_transition(tmp_path: Path) -> None:
    """CF-13: a header naming the old private workbench repository is accepted only while the export's ``repo``
    record says ``transition``; marked ``retired`` (P7) every such header is a finding. Since
    P4's rewrite no header in the tree carries an old slug, so the rule is shown on a planted
    one."""
    import copy

    export = li.load()
    assert old_slug_violations(export) == []
    # The private organisation's pairs are withheld from the public variant: skipped BY NAME on
    # the mirror snapshot only (li.load refuses the public fallback anywhere else).
    li.require_repo(export, li.OLD_PRIVATE_WORKBENCH)
    (tmp_path / "planted.py").write_text(
        f"# Origin: miStudio ({li.OLD_PRIVATE_WORKBENCH}) backend/src/x.py @ c829a2cc\n"
    )
    finding = [f"planted.py: {li.OLD_PRIVATE_WORKBENCH}"]
    as_status = {}
    for status in ("transition", "retired"):
        as_status[status] = copy.deepcopy(export)
        for record in as_status[status].section("repo"):
            if record["old"] == li.OLD_PRIVATE_WORKBENCH:
                record["status"] = status
    assert old_slug_violations(as_status["transition"], [tmp_path]) == []
    assert old_slug_violations(as_status["retired"], [tmp_path]) == finding
    # The committed export answers by its own record (P7 retires it: the planted header is a
    # finding).
    committed = li.require_repo(export, li.OLD_PRIVATE_WORKBENCH)["status"]
    assert old_slug_violations(export, [tmp_path]) == ([] if committed == "transition" else finding)


# --- feature 020 P4 (FR-020.20; FTASKS 15.6): headers and rows name the origins' new names ------

ORIGINS = {
    "miWorkbench": "Onegaishimas/miWorkbench",
    "miServer": "Onegaishimas/miServer",
    "miForge": "Onegaishimas/miForge",
}
#: Any origin line, whatever product it names: the net the new-name pattern cannot be.
ANY_ORIGIN = re.compile(r"Origin[^:\n]*:\s*(mi[A-Za-z]+)\s*\(([^)]+)\)")
OLD_SIBLING = re.compile(r"(?i)mi(studio|llm)")


def origin_lines() -> list[tuple[str, str]]:
    """``(file, line)`` of every origin line in the scanned trees, anywhere in the file."""
    found = []
    for root in SCANNED:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            for line in path.read_text(errors="ignore").splitlines():
                if ANY_ORIGIN.search(line):
                    found.append((str(path.relative_to(REPO)), line))
    return found


def test_no_header_names_an_old_sibling_after_p4() -> None:
    """Every origin line names its origin by the product's and the repository's NEW name; the
    commit it cites is unchanged and still resolves in the renamed repository (PD-14)."""
    lines = origin_lines()
    assert len(lines) >= EXPECTED_COPIED - 2
    wrong = []
    for where, line in lines:
        product, slug = ANY_ORIGIN.search(line).groups()  # type: ignore[union-attr]
        if ORIGINS.get(product) != slug or OLD_SIBLING.search(line):
            wrong.append(f"{where}: {line.strip()[:120]}")
    assert wrong == [], wrong[:10]


#: Any register row, whatever product it names (origin | `path` | `commit` | ...).
ANY_ROW = re.compile(r"^\|\s*([^|`]+?)\s*\|\s*`[^`]+`\s*\|\s*`[0-9a-f]{7,40}`\s*\|", re.M)


def test_no_register_row_names_an_old_sibling_after_p4() -> None:
    text = source_only("docs/REUSE.md").read_text()
    labels = ANY_ROW.findall(text)
    assert len(labels) == EXPECTED_COPIED, len(labels)
    assert set(labels) <= set(ORIGINS), sorted(set(labels) - set(ORIGINS))
    assert not OLD_SIBLING.search(text), OLD_SIBLING.findall(text)


def test_the_header_and_row_patterns_know_exactly_the_new_names() -> None:
    """The patterns and the rewrite move in one commit: a pattern still naming an old product
    would keep matching nothing and read as a clean register."""
    for pattern in (HEADER.pattern, ROW.pattern):
        assert "(miWorkbench|miServer|miForge)" in pattern, pattern
