"""Every recorded Hugging Face body parses into the client's shapes (001 FTASKS 4.8).

If HF changes a response shape, a re-recorded fixture fails here before production does.
"""

from __future__ import annotations

import json

import pytest

from src.services.sources.revision import resolve_revision
from tests.support.hf_mock import (
    COLBERT,
    FIXTURES,
    GATED,
    HUMICROEDIT,
    HUMICROEDIT_SHA,
    OFFENSIVE,
    HfMock,
    fixture,
    hub_client,
)


def test_every_fixture_is_listed_with_its_capture_date() -> None:
    readme = (FIXTURES / "README.md").read_text()
    for path in FIXTURES.glob("*.json"):
        json.loads(path.read_text())
        assert f"`{path.name}`" in readme, f"{path.name} is not described in the fixtures README"
    assert "2026-10-06" in readme


@pytest.mark.parametrize(
    ("repo", "ref", "commit"),
    [
        (COLBERT, "2bb7d6bc", "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac"),
        (HUMICROEDIT, HUMICROEDIT_SHA, HUMICROEDIT_SHA),
        (OFFENSIVE, "04b8f88d", None),
        (GATED, None, "200748d9d3cddcc9d782887541057aca0b18c5da"),
    ],
)
def test_hub_bodies_parse_into_a_resolved_revision(
    repo: str, ref: str | None, commit: str | None
) -> None:
    hub, _ = hub_client(HfMock())
    resolved = resolve_revision(hub, repo, ref)
    assert len(resolved.commit) == 40
    if commit:
        assert resolved.commit == commit


def test_the_gated_fixture_says_gated() -> None:
    hub, _ = hub_client(HfMock())
    assert resolve_revision(hub, GATED, None).gated == "auto"


def test_viewer_bodies_parse() -> None:
    hub, _ = hub_client(HfMock())
    assert [(s.config, s.split) for s in hub.viewer_splits(COLBERT)] == [("default", "train")]
    assert hub.viewer_size(COLBERT, "default")["size"]["config"]["num_rows"] == 200_000
    assert hub.viewer_size(COLBERT, None)["size"]["dataset"]["num_rows"] == 200_000
    first = hub.viewer_first_rows(COLBERT, "default", "train")
    assert {f["name"] for f in first["features"]} >= {"text", "humor"}
    assert first["rows"][0]["row"]["text"]


def test_the_multi_config_fixture_has_two_configs() -> None:
    hub, _ = hub_client(HfMock())
    assert {s.config for s in hub.viewer_splits(HUMICROEDIT)} == {"subtask-1", "subtask-2"}
    assert fixture("humicroedit_revision.json")["sha"] == HUMICROEDIT_SHA
