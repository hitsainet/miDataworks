"""Hub planning and verification (FR-008.21, 008.22, 008.24, 008.64, 008.65; FTASKS 8.3, 8.7).

EC-3 tampered hash, missing file, extra file; EC-8 no change; EC-13 oversize; EC-14 foreign.
"""

from __future__ import annotations

import pytest

from src.services.publishing.card import assemble
from src.services.publishing.hub_plan import (
    BuiltFile,
    Plan,
    Refusal,
    RemoteFile,
    RepoState,
    plan,
)
from src.services.publishing.verify import check_configs, compare

SPLIT = BuiltFile(
    "data/train.parquet", "/x/train.parquet", "split", "train", 500, "a" * 64, "1" * 40
)
CARD = BuiltFile("README.md", "/x/README.md", "card", None, 80, "b" * 64, "2" * 40)
MANIFEST = BuiltFile(
    "midataworks-dataset-version.json", "/x/m.json", "manifest", None, 90, "c" * 64, "3" * 40
)
BUILT = [SPLIT, CARD, MANIFEST]


def remote_for(files: list[BuiltFile]) -> tuple[RemoteFile, ...]:
    out = [RemoteFile(".gitattributes", 40, "9" * 40, None)]
    for f in files:
        if f.path.endswith(".parquet"):
            out.append(RemoteFile(f.path, f.bytes, "8" * 40, f.sha256))
        else:
            out.append(RemoteFile(f.path, f.bytes, f.git_blob_sha1, None))
    return tuple(out)


def test_a_new_repository_gets_every_file_and_no_parent() -> None:
    result = plan(RepoState(False, None, None), BUILT, max_bytes=10**9, prior_dw_paths=set())
    assert isinstance(result, Plan) and not result.no_change
    assert result.adds == tuple(BUILT) and result.deletes == () and result.parent_commit is None


def test_an_existing_repository_commits_against_its_head_and_deletes_stale_dw_paths() -> None:
    state = RepoState(
        True,
        True,
        "h" * 40,
        remote_for([CARD]) + (RemoteFile("data/old.parquet", 9, None, "e" * 64),),
    )
    result = plan(state, BUILT, max_bytes=10**9, prior_dw_paths={"README.md", "data/old.parquet"})
    assert isinstance(result, Plan)
    assert result.parent_commit == "h" * 40
    assert result.deletes == ("data/old.parquet",)


def test_ec8_identical_digests_are_no_change() -> None:
    state = RepoState(True, True, "h" * 40, remote_for(BUILT))
    prior = {f.path for f in BUILT}
    result = plan(state, BUILT, max_bytes=10**9, prior_dw_paths=prior)
    assert isinstance(result, Plan) and result.no_change and result.adds == ()


def test_ec14_a_foreign_file_refuses_and_gitattributes_does_not() -> None:
    state = RepoState(
        True, True, "h" * 40, remote_for([]) + (RemoteFile("notes.txt", 3, "7" * 40, None),)
    )
    result = plan(state, BUILT, max_bytes=10**9, prior_dw_paths=set())
    assert isinstance(result, Refusal) and result.code == "repo_has_foreign_files"
    assert result.details["paths"] == ["notes.txt"]
    clean = plan(
        RepoState(True, True, "h" * 40, remote_for([])),
        BUILT,
        max_bytes=10**9,
        prior_dw_paths=set(),
    )
    assert isinstance(clean, Plan)


def test_ec14_a_target_path_someone_else_wrote_is_foreign() -> None:
    state = RepoState(True, True, "h" * 40, remote_for([CARD]))
    result = plan(state, BUILT, max_bytes=10**9, prior_dw_paths=set())
    assert isinstance(result, Refusal) and result.details["paths"] == ["README.md"]


def test_ec13_an_oversize_split_refuses_naming_it() -> None:
    result = plan(RepoState(False, None, None), BUILT, max_bytes=499, prior_dw_paths=set())
    assert isinstance(result, Refusal) and result.code == "split_too_large"
    assert result.details == {"split": "train", "bytes": 500, "limit": 499}
    assert isinstance(
        plan(RepoState(False, None, None), BUILT, max_bytes=500, prior_dw_paths=set()), Plan
    )


def test_verification_passes_when_every_hash_matches() -> None:
    result = compare(BUILT, remote_for(BUILT))
    assert result.ok and all(f.match for f in result.files) and len(result.files) == 3


def test_ec3_a_tampered_lfs_hash_fails() -> None:
    remote = list(remote_for(BUILT))
    remote[1] = RemoteFile(SPLIT.path, SPLIT.bytes, "8" * 40, "0" * 64)
    result = compare(BUILT, remote)
    assert not result.ok and result.mismatched == [SPLIT.path]


def test_a_wrong_lfs_size_fails() -> None:
    remote = list(remote_for(BUILT))
    remote[1] = RemoteFile(SPLIT.path, SPLIT.bytes + 1, "8" * 40, SPLIT.sha256)
    assert not compare(BUILT, remote).ok


def test_a_tampered_small_file_fails_on_its_blob_id() -> None:
    remote = list(remote_for(BUILT))
    remote[2] = RemoteFile(CARD.path, CARD.bytes, "0" * 40, None)
    result = compare(BUILT, remote)
    assert not result.ok and result.mismatched == [CARD.path]


def test_a_missing_file_fails() -> None:
    result = compare(BUILT, remote_for([SPLIT, CARD]))
    assert not result.ok and result.missing == [MANIFEST.path]


def test_an_extra_file_fails() -> None:
    result = compare(BUILT, remote_for(BUILT) + (RemoteFile("stray.txt", 1, "6" * 40, None),))
    assert not result.ok and result.extra == ["stray.txt"]


def test_the_configs_block_must_map_exactly_the_splits() -> None:
    fm = {
        "configs": [
            {
                "config_name": "default",
                "data_files": [{"split": "train", "path": "data/train.parquet"}],
            }
        ]
    }
    card = assemble(fm, "prose", "record")
    assert check_configs(card, {"train": "data/train.parquet"}) == (True, None)
    ok, problem = check_configs(card, {"train": "data/train.parquet", "test": "data/test.parquet"})
    assert not ok and problem
    assert check_configs(b"no front matter", {"train": "x"})[0] is False


@pytest.mark.parametrize("bad", [None, "z" * 64])
def test_a_remote_with_no_hash_never_matches(bad: str | None) -> None:
    remote = [RemoteFile(f.path, f.bytes, bad if f.role != "split" else None, None) for f in BUILT]
    assert not compare(BUILT, remote).ok
