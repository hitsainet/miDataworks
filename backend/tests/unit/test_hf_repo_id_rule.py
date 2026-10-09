"""The repository-ID rule, Python side of the shared case file (001 FTASKS 10.2, 10.3).

``docs/schemas/hf-repo-id-cases.json`` is read by this test and by the frontend's
``utils/hfRepoId.test.ts``; both must give the same verdict for every case. A malformed ID is
refused by the request model with error type ``repo_id_invalid``, before any network call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.schemas.sources import HfImportRequest, HfPreviewRequest

CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "docs" / "schemas" / "hf-repo-id-cases.json").read_text()
)


@pytest.mark.parametrize("repo_id", CASES["valid"])
@pytest.mark.parametrize("model", [HfPreviewRequest, HfImportRequest])
def test_valid_ids_are_accepted(model: type, repo_id: str) -> None:
    assert model(repo_id=repo_id).repo_id == repo_id


@pytest.mark.parametrize("repo_id", [*CASES["invalid"], CASES["too_long"]])
@pytest.mark.parametrize("model", [HfPreviewRequest, HfImportRequest])
def test_invalid_ids_are_refused_as_repo_id_invalid(model: type, repo_id: str) -> None:
    with pytest.raises(ValidationError) as info:
        model(repo_id=repo_id)
    assert [e["type"] for e in info.value.errors()] == ["repo_id_invalid"]


def test_the_too_long_case_is_only_too_long() -> None:
    long = CASES["too_long"]
    assert len(long) > 96 and long.count("/") == 1


def test_the_case_file_has_both_kinds() -> None:
    assert len(CASES["valid"]) >= 5 and len(CASES["invalid"]) >= 8
