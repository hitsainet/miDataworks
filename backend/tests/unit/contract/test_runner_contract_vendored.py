"""miStudio's and miLLM's vendored copies of both runner contracts equal ours (FR-018.14; FTASKS 3.10).

Each consumer keeps its copy at ``docs/schemas/<file>``. Skipped loudly, naming the file, when a
sibling checkout lacks it (always on the public mirror and in CI, and until miStudio 040 S4 / miLLM
039 S3 vendor it); ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` turns the skip into a failure.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.support.siblings import sibling_repo
from tests.support.source_tree import REPO

SIBLINGS = {"MISTUDIO_REPO": "miStudio", "MILLM_REPO": "miLLM"}
FILES = ("midataworks-runner-v1.json", "runner-cases-v1.json")
COPY_AT = "docs/schemas/{file}"


def _copy(var: str, file: str) -> Path | None:
    root = sibling_repo(SIBLINGS[var], var)
    copy = root / COPY_AT.format(file=file)
    if copy.exists():
        return copy
    message = f"{var}: no vendored {file} at {copy}. Copy miDataworks' docs/schemas/{file} there."
    if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
        pytest.fail(message)
    pytest.skip("CROSS-REPO: " + message)
    return None


@pytest.mark.parametrize("file", FILES)
@pytest.mark.parametrize("var", sorted(SIBLINGS))
def test_the_vendored_copy_is_byte_identical(var: str, file: str) -> None:
    copy = _copy(var, file)
    assert copy is not None
    assert (
        copy.read_bytes() == (REPO / "docs" / "schemas" / file).read_bytes()
    ), f"{copy} differs from miDataworks' docs/schemas/{file}; copy it over, never edit it by hand"


def _fake_sibling(tmp_path: Path, file: str, content: bytes) -> None:
    target = tmp_path / COPY_AT.format(file=file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


@pytest.mark.parametrize("file", FILES)
def test_a_one_byte_difference_is_red_and_an_identical_copy_green(
    file: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours = (REPO / "docs" / "schemas" / file).read_bytes()
    monkeypatch.setenv("MILLM_REPO", str(tmp_path))
    _fake_sibling(tmp_path, file, ours)
    test_the_vendored_copy_is_byte_identical("MILLM_REPO", file)
    _fake_sibling(tmp_path, file, ours[:-2] + b" \n")
    with pytest.raises(AssertionError):
        test_the_vendored_copy_is_byte_identical("MILLM_REPO", file)


def test_an_absent_copy_skips_naming_the_file_and_fails_when_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTUDIO_REPO", str(tmp_path))
    with pytest.raises(pytest.skip.Exception, match="runner-cases-v1.json"):
        _copy("MISTUDIO_REPO", FILES[1])
    monkeypatch.setenv("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS", "1")
    with pytest.raises(pytest.fail.Exception, match="midataworks-runner-v1.json"):
        _copy("MISTUDIO_REPO", FILES[0])
