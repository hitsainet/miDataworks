"""Revision resolution (001 FTASKS 4.3, 4.5; FR-001.2; mutation control M1).

Empty, ``main``, a tag, the short SHA ``2bb7d6bc`` and the full SHA each resolve to the full
commit and keep the requested ref. An empty ref reads the repository root (never rewritten to
``main``). A ref HF cannot resolve, or an answer without a full 40-hex commit, raises
``revision_unresolved``; the requested ref is NEVER used as the pin.
"""

from __future__ import annotations

import pytest

from src.core.errors import AppError
from src.services.sources.revision import resolve_revision
from tests.support.hf_mock import COLBERT, COMMIT, HEAD_SHA, HfMock, fixture, hub_client


@pytest.mark.parametrize("ref", [None, "", "  ", "main", "v1.0", "2bb7d6bc", COMMIT])
def test_every_form_resolves_to_the_full_commit_and_keeps_the_ref(ref: str | None) -> None:
    hub, seen = hub_client(HfMock())
    resolved = resolve_revision(hub, COLBERT, ref)
    assert resolved.commit == COMMIT
    assert resolved.requested_ref == ((ref or "").strip() or None)


def test_an_empty_ref_reads_the_repository_root_never_main() -> None:
    hub, seen = hub_client(HfMock())
    resolve_revision(hub, COLBERT, "")
    assert [r.url.path for r in seen] == [f"/api/datasets/{COLBERT}"]


def test_a_short_sha_goes_through_the_revision_route() -> None:
    hub, seen = hub_client(HfMock())
    resolve_revision(hub, COLBERT, "2bb7d6bc")
    assert seen[0].url.path == f"/api/datasets/{COLBERT}/revision/2bb7d6bc"


def test_the_default_head_is_recorded_for_the_preview_note() -> None:
    mock = HfMock()
    mock.head = HEAD_SHA
    hub, _ = hub_client(mock)
    assert resolve_revision(hub, COLBERT, None).default_head == HEAD_SHA
    assert resolve_revision(hub, COLBERT, "2bb7d6bc").default_head is None


def test_an_unresolvable_ref_raises_revision_unresolved() -> None:
    hub, _ = hub_client(
        {
            f"/api/datasets/{COLBERT}/revision/no-such-ref-zz": (
                404,
                fixture("missing_revision.json"),
            )
        }
    )
    with pytest.raises(AppError) as info:
        resolve_revision(hub, COLBERT, "no-such-ref-zz")
    assert info.value.code == "revision_unresolved"
    assert info.value.details == {"revision": "no-such-ref-zz"}


@pytest.mark.parametrize(
    "bad", ["2bb7d6bc", "main", "", None, "2BB7D6BCE15E42C2A3CF2BE8305FA3049929D3AC"]
)
def test_an_answer_without_a_full_lowercase_commit_is_refused_never_used(bad: str | None) -> None:
    body = dict(fixture("colbert_revision_short.json"), sha=bad)
    hub, _ = hub_client({f"/api/datasets/{COLBERT}/revision/main": (200, body)})
    with pytest.raises(AppError) as info:
        resolve_revision(hub, COLBERT, "main")
    assert info.value.code == "revision_unresolved"


def test_the_card_licence_tags_gated_and_files_come_from_the_resolved_answer() -> None:
    hub, _ = hub_client(HfMock())
    resolved = resolve_revision(hub, COLBERT, "2bb7d6bc")
    assert resolved.card_license == "cc-by-2.0"
    assert resolved.gated == "false"
    assert "dataset.csv" in resolved.siblings
