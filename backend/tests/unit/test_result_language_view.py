"""``approval_view``'s contract limits (022 FR-022.3; review R2 finding 6), with the values written out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.services.result_language import approval_view
from tests.support.result_language import validate

NOW = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


@dataclass
class Row:
    id: str = "apr_1"
    status: str = "pending"
    expires_at: datetime = NOW + timedelta(hours=1)
    summary: str = "Publish ver_1"
    action: str = "hub_push"


def test_a_summary_over_500_characters_is_cut() -> None:
    view = approval_view(Row(summary="s" * 900), NOW)
    assert len(view["summary"]) == 500 and view["summary"].startswith("s" * 499)
    validate("approval", view)


def test_an_empty_summary_names_the_action() -> None:
    for summary in ("", "   "):
        view = approval_view(Row(summary=summary), NOW)
        assert view["summary"] == "hub_push"
        validate("approval", view)


def test_the_expiry_boundary_reads_expired() -> None:
    assert approval_view(Row(expires_at=NOW), NOW)["status"] == "expired"
    assert approval_view(Row(expires_at=NOW + timedelta(seconds=1)), NOW)["status"] == "pending"
    assert (
        approval_view(Row(status="executed", expires_at=NOW - timedelta(days=1)), NOW)["status"]
        == "executed"
    )
