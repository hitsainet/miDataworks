"""The public variant of the rebrand export (feature 020; CF-2; FTASKS 1.3).

The mirror receives only ``docs/legacy-identifiers.public.md``. It must hold exactly the full file's
records minus the ``withheld`` ones (control M41), and a test reading it must skip a withheld id BY
NAME, one skip per id, never the whole module (control M41b).
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from tests.support import legacy_identifiers as li
from tests.support.source_tree import source_only


def _without_withheld_flag(record: dict[str, object]) -> dict[str, object]:
    if record["section"] != "entry":
        return record
    flags = [f for f in record["flags"] if f != "public_withheld"]  # type: ignore[attr-defined]
    return {**record, "flags": flags}


def test_public_records_equal_full_minus_withheld() -> None:
    # The full file is private: skipped LOUDLY on the mirror snapshot only, a failure elsewhere.
    full = li.load(source_only(li.FULL.as_posix()))
    public = li.load(li.REPO / li.PUBLIC)
    assert full.variant == "full" and public.variant == "public"
    assert full.header["withheld"] == []
    withheld = set(public.withheld())
    assert withheld, "the public variant withholds nothing: the test would compare equal files"
    kept = [r for r in full.records if not (li.record_id(r) & withheld)]
    dropped = [r for r in full.records if li.record_id(r) & withheld]
    assert len(dropped) == len(withheld), "a withheld id names no record of the full file"
    assert public.records == [_without_withheld_flag(r) for r in kept]
    for record in dropped:
        if record["section"] == "entry":
            assert "public_withheld" in record["flags"], record["id"]
    for key in ("repo", "register_hash", "phase", "closed"):
        assert public.header[key] == full.header[key], key


def test_loader_on_public_only_tree_skips_withheld_by_name(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    shutil.copy2(li.REPO / li.PUBLIC, tmp_path / li.PUBLIC)
    export = li.load(root=tmp_path)
    assert export.variant == "public"
    # Every entry id: the full export's here; on the mirror snapshot, which has no full export, the
    # public entries plus the ids its header withholds (li.load refuses that fallback elsewhere).
    reference = li.load()
    ids = [e["id"] for e in reference.entries()]
    ids += [w for w in reference.withheld() if w.startswith("DR-")]
    skipped: list[str] = []
    found: list[str] = []
    for entry_id in ids:
        try:
            li.require_entry(export, entry_id)
        except pytest.skip.Exception as skip:
            assert entry_id in str(skip), str(skip)
            skipped.append(entry_id)
        else:
            found.append(entry_id)
    withheld_entries = sorted(w for w in export.withheld() if w.startswith("DR-"))
    assert withheld_entries, "the public variant withholds no entry: nothing would be skipped"
    assert sorted(skipped) == withheld_entries
    assert len(found) == len(ids) - len(withheld_entries)
    assert found, "every entry was skipped"


def test_a_tree_with_neither_file_fails(tmp_path: Path) -> None:
    with pytest.raises(li.ExportError, match="neither"):
        li.load(root=tmp_path)


def test_a_withheld_guard_is_skipped_by_name(tmp_path: Path) -> None:
    """The public file withholds G-STUDIO-PATH (a home path); a guard test on the mirror skips it
    by name, while on this tree the full file serves it."""
    (tmp_path / "docs").mkdir()
    shutil.copy2(li.REPO / li.PUBLIC, tmp_path / li.PUBLIC)
    public = li.load(root=tmp_path)
    assert "G-STUDIO-PATH" in public.withheld()
    with pytest.raises(pytest.skip.Exception, match="G-STUDIO-PATH withheld"):
        li.require_guard(public, "G-STUDIO-PATH")
    assert li.require_guard(li.load(), "G-STUDIO-PATH")["effect"] == "allow"


def test_a_withheld_repo_is_skipped_by_name(tmp_path: Path) -> None:
    """The public file withholds the private organisation's repo pairs by digest; a test that
    needs one skips it BY that id on the mirror, while the full file serves it here."""
    (tmp_path / "docs").mkdir()
    shutil.copy2(li.REPO / li.PUBLIC, tmp_path / li.PUBLIC)
    public = li.load(root=tmp_path)
    with pytest.raises(pytest.skip.Exception, match=r"repo:sha256:[0-9a-f]{16} withheld"):
        li.require_repo(public, li.OLD_PRIVATE_WORKBENCH)
    # The public record says what the full one says (P7 retires both, CF-13).
    full_status = li.require_repo(li.load(), "hitsainet/miStudio")["status"]
    assert li.require_repo(public, "hitsainet/miStudio")["status"] == full_status
    assert full_status in ("transition", "retired")
    # A FULL export never skips: a record it lacks is a failure (a skip would read as a pass).
    as_full = copy.deepcopy(public)
    as_full.header["variant"] = "full"
    for export, old in ((public, "nobody/miNothing"), (as_full, li.OLD_PRIVATE_WORKBENCH)):
        try:
            li.require_repo(export, old)
        except pytest.skip.Exception as skip:
            pytest.fail(f"require_repo skipped {old} on a {export.variant} export: {skip}")
        except AssertionError as exc:
            assert "is not in" in str(exc)
        else:
            pytest.fail(f"require_repo returned a record for {old}")


@pytest.mark.parametrize("shape", ["none", "private", "shallow-private", "orphan-plain", "mirror"])
def test_the_public_fallback_needs_the_mirror_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """On this repository's own tree only the mirror snapshot may read the public export in place
    of the full one; anywhere else the missing full export fails, so no withheld-record skip can
    fire on the private CI (mirror CI, 2026-11-08)."""
    from tests.support.git_snapshots import EXPECTED, make_repo

    tree = make_repo(tmp_path / "t", shape)
    (tree / "docs").mkdir()
    shutil.copy2(li.REPO / li.PUBLIC, tree / li.PUBLIC)
    monkeypatch.setattr(li, "REPO", tree)
    if EXPECTED[shape]:
        assert li.load(root=tree).variant == "public"
    else:
        with pytest.raises(li.ExportError, match="not the mirror snapshot"):
            li.load(root=tree)
