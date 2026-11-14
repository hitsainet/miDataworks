"""Previous-address equivalence, the pure rules (feature 020 part C; FR-020.12; FTASKS 6.3).

Three hosts and a spelling variant, so no fixture agrees with the code by construction (FTDD
§10.1): ``old`` (a listed previous origin), ``new`` (the current address), ``other`` (unrelated),
and ``old/`` (the same origin as ``old``, a different text).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import select

from src.core import sibling_origins as so
from src.core.config import get_settings
from src.core.sibling_origins import Sibling

OLD = "http://old.test:8000"
OLD_SLASH = "http://old.test:8000/"
NEW = "http://new.test:8000"
OTHER = "http://other.test:8000"


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", NEW)
    monkeypatch.setattr(settings, "mistudio_base_url", NEW)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", (OLD,))
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", (OLD,))
    yield


@pytest.fixture
def no_previous(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", NEW)
    monkeypatch.setattr(settings, "mistudio_base_url", NEW)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", ())
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", ())
    yield


@pytest.mark.parametrize("sibling", list(Sibling))
def test_group_is_current_plus_previous(configured: None, sibling: Sibling) -> None:
    assert so.group(sibling) == frozenset({NEW, OLD})


def test_group_reads_each_sibling_its_own_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", "http://serve.test:1")
    monkeypatch.setattr(settings, "mistudio_base_url", "http://bench.test:2")
    monkeypatch.setattr(settings, "miserver_previous_base_urls", ("http://serve-old.test:1",))
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", ("http://bench-old.test:2",))
    assert so.group(Sibling.MISERVER) == {"http://serve.test:1", "http://serve-old.test:1"}
    assert so.group(Sibling.MIWORKBENCH) == {"http://bench.test:2", "http://bench-old.test:2"}


def test_same_sibling_exact_text_still_matches(no_previous: None) -> None:
    assert so.same_sibling(Sibling.MISERVER, OTHER, OTHER)
    assert so.same_sibling(Sibling.MISERVER, None, None)


def test_same_sibling_across_a_previous_origin(configured: None) -> None:
    assert so.same_sibling(Sibling.MISERVER, NEW, OLD)
    assert so.same_sibling(Sibling.MISERVER, OLD_SLASH, NEW)
    assert so.same_sibling(Sibling.MIWORKBENCH, OLD, NEW)


def test_empty_previous_list_behaves_as_today(no_previous: None) -> None:
    """Opt-in: with no previous origin listed, two spellings of one origin are NOT the same
    sibling, exactly as the ``==`` the sites used before (control M54)."""
    assert not so.same_sibling(Sibling.MISERVER, OLD_SLASH, OLD)
    assert not so.same_sibling(Sibling.MISERVER, NEW + "/", NEW)
    assert so.call_base(Sibling.MIWORKBENCH, OLD) == OLD
    assert so.previous_sibling_of(OLD) is None


def test_unrelated_host_is_not_the_same_sibling(configured: None) -> None:
    assert not so.same_sibling(Sibling.MISERVER, NEW, OTHER)
    assert not so.same_sibling(Sibling.MISERVER, OLD, OTHER)
    assert not so.same_sibling(Sibling.MISERVER, NEW, None)


class _Session:
    """Records the one SELECT and answers stored values (no database needed for the rule)."""

    def __init__(self, stored: list[str]) -> None:
        self.stored = stored
        self.statements: list[Any] = []

    def scalars(self, statement: Any) -> list[str]:
        self.statements.append(statement)
        return list(self.stored)


def test_matching_values_filters_stored_values_by_origin(configured: None) -> None:
    from src.models.detector_results import RewardMark

    session = _Session([OLD, OLD_SLASH, NEW, OTHER, "http://old.test:9000"])
    values = so.matching_values(session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, NEW)
    assert values == sorted([NEW, OLD, OLD_SLASH])
    assert len(session.statements) == 1
    assert "RECURSIVE" in str(session.statements[0]).upper(), "the loose index scan"


def test_matching_values_outside_the_group_is_the_value_alone(configured: None) -> None:
    from src.models.detector_results import RewardMark

    session = _Session([OLD, NEW])
    assert so.matching_values(
        session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, OTHER
    ) == [OTHER]
    assert session.statements == [], "no query for an address outside the group"


def test_matching_values_matches_a_path_value_by_exact_text_only(configured: None) -> None:
    from src.models.detector_results import RewardMark

    session = _Session([OLD + "/api", NEW])
    values = so.matching_values(session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, NEW)
    assert values == [NEW], "a stored value with a path is matched by its exact text only"
    session = _Session([OLD + "/api"])
    assert so.matching_values(
        session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, OLD + "/api"
    ) == [OLD + "/api"]


def test_call_base_rewrites_only_a_previous_origin(
    configured: None, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="src.core.sibling_origins")
    assert so.call_base(Sibling.MIWORKBENCH, OLD) == NEW
    assert so.call_base(Sibling.MIWORKBENCH, OLD_SLASH) == NEW
    assert so.call_base(Sibling.MIWORKBENCH, NEW) == NEW
    assert so.call_base(Sibling.MIWORKBENCH, OTHER) == OTHER
    rewrites = [r for r in caplog.records if "sibling_previous_origin_rewrite" in r.getMessage()]
    assert len(rewrites) == 2
    assert all(OLD in r.getMessage() and NEW in r.getMessage() for r in rewrites)


def test_call_base_keeps_stored_when_current_unset(
    configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "mistudio_base_url", None)
    assert so.call_base(Sibling.MIWORKBENCH, OLD) == OLD


def test_previous_sibling_of(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", NEW)
    monkeypatch.setattr(settings, "mistudio_base_url", OTHER)
    monkeypatch.setattr(settings, "miserver_previous_base_urls", (OLD,))
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", ("http://bench-old.test:1",))
    assert so.previous_sibling_of(OLD + "/v1") is Sibling.MISERVER
    assert so.previous_sibling_of("http://bench-old.test:1") is Sibling.MIWORKBENCH
    assert so.previous_sibling_of(NEW) is None, "the current address is not a previous one"
    assert so.previous_sibling_of(None) is None


def test_current_address_keeps_the_role_path(configured: None) -> None:
    """ID-8: an endpoint role's base URL carries ``/v1``; the re-point keeps it."""
    assert so.current_address_for(OLD + "/v1", Sibling.MISERVER) == NEW + "/v1"
    assert so.current_address_for(OLD + "/v1?x=1", Sibling.MISERVER) == NEW + "/v1?x=1"
    assert so.current_address_for(OTHER + "/v1", Sibling.MISERVER) is None


def test_matching_values_runs_one_distinct_select_on_postgres(
    configured: None, clean_db: None
) -> None:
    """The real query against the real table: one SELECT DISTINCT of the address column."""
    from src.core.database import sync_session_factory
    from src.models.detector_results import RewardMark

    with sync_session_factory()() as session:
        assert so.matching_values(
            session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, NEW
        ) == [NEW]
        assert session.scalars(select(RewardMark.id)).all() == []


def test_empty_list_issues_no_query_for_the_current_address(no_previous: None) -> None:
    """Review control R12: with no previous origin listed, ``matching_values`` is today's ``==``
    without a query, even for the CURRENT address and a stored spelling variant of it."""
    from src.models.detector_results import RewardMark

    session = _Session([NEW + "/", NEW])
    values = so.matching_values(session, RewardMark.mistudio_base_url, Sibling.MIWORKBENCH, NEW)
    assert values == [NEW]
    assert session.statements == [], "no query when the feature is not configured"


def test_same_sibling_never_crosses_a_path(configured: None) -> None:
    """Review round 2: an address with a path is the same sibling only as an address with the SAME
    path (the module's rule; matching_values keeps path values to their exact text). A stored
    ``http://old.test:8000/staging`` is not the instance at ``http://new.test:8000`` (control M79).
    An endpoint role's ``/v1`` on both sides still is (R1/R4 resume across origins)."""
    assert not so.same_sibling(Sibling.MIWORKBENCH, NEW, OLD + "/staging")
    assert not so.same_sibling(Sibling.MIWORKBENCH, OLD + "/staging", NEW + "/other")
    assert so.same_sibling(Sibling.MISERVER, NEW + "/v1", OLD + "/v1")
    assert so.same_sibling(Sibling.MISERVER, NEW + "/v1/", OLD + "/v1")
    assert not so.same_sibling(Sibling.MISERVER, NEW + "/v1", OLD + "/v2")
    assert so.same_sibling(Sibling.MIWORKBENCH, OLD + "/staging", OLD + "/staging"), "equal text"


def test_a_previous_origin_of_one_sibling_is_never_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 2: two siblings whose lists share an origin (one instance once served both,
    or a typo) stay apart: each judges only by its own current and previous origins."""
    settings = get_settings()
    monkeypatch.setattr(settings, "millm_base_url", "http://serve.test:1")
    monkeypatch.setattr(settings, "mistudio_base_url", "http://bench.test:2")
    monkeypatch.setattr(settings, "miserver_previous_base_urls", ("http://shared.test:9",))
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", ())
    assert so.same_sibling(Sibling.MISERVER, "http://shared.test:9", "http://serve.test:1")
    assert not so.same_sibling(Sibling.MIWORKBENCH, "http://shared.test:9", "http://bench.test:2")
    assert not so.same_sibling(Sibling.MIWORKBENCH, "http://shared.test:9", "http://serve.test:1")
    monkeypatch.setattr(settings, "miworkbench_previous_base_urls", ("http://shared.test:9",))
    assert so.same_sibling(Sibling.MIWORKBENCH, "http://shared.test:9", "http://bench.test:2")
    assert not so.same_sibling(Sibling.MIWORKBENCH, "http://bench.test:2", "http://serve.test:1")


def test_same_sibling_needs_the_same_query_and_fragment(configured: None) -> None:
    """Review round 3 (survivor S03): beside the path, the query and fragment must agree too (the
    docstring's rule); a trailing slash on a bare origin is still no difference."""
    assert not so.same_sibling(Sibling.MISERVER, NEW + "/v1?tenant=a", OLD + "/v1?tenant=b")
    assert not so.same_sibling(Sibling.MISERVER, NEW + "/v1#a", OLD + "/v1#b")
    assert not so.same_sibling(Sibling.MISERVER, NEW + "/v1", OLD + "/v1?tenant=a")
    assert so.same_sibling(Sibling.MISERVER, NEW + "/v1?tenant=a", OLD + "/v1/?tenant=a")
    assert so.same_sibling(Sibling.MISERVER, NEW + "/", OLD)
    assert so.same_sibling(Sibling.MISERVER, NEW, OLD + "/")
