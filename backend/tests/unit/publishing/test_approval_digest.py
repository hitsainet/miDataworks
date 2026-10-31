"""The request digest an approval binds to (FR-008.51; FTDD 008 section 5.5; EC-15)."""

from __future__ import annotations

from typing import Any

from src.services.publishing.approval_digest import publish_request_digest

FILES = [
    {"path": "data/train.parquet", "sha256": "a" * 64},
    {"path": "data/test.parquet", "sha256": "b" * 64},
]


def d(**kw: Any) -> str:
    base: dict[str, Any] = {
        "kind": "publish",
        "version_id": "v1",
        "build_id": "pbld_1",
        "build_files": FILES,
        "repo_id": "mistudio/humor",
        "visibility": "private",
        "card_prose": "# Humor",
    }
    base.update(kw)
    return publish_request_digest(**base)


def test_every_field_is_bound() -> None:
    base = d()
    for change in (
        {"kind": "card_only"},
        {"version_id": "v2"},
        {"build_id": "pbld_2"},
        {"build_files": [FILES[0]]},
        {"build_files": [FILES[0], {"path": "data/test.parquet", "sha256": "c" * 64}]},
        {"repo_id": "mistudio/other"},
        {"visibility": "public"},
        {"card_prose": "# Humor!"},
    ):
        assert d(**change) != base, change


def test_file_order_does_not_change_the_digest() -> None:
    assert d(build_files=list(reversed(FILES))) == d()
