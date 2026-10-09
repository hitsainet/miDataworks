"""One HF status mapping for the Hub API and the Dataset Viewer (001 FTASKS 4.2, 4.4; FTDD 5.4).

404 → ``hf_not_found``; 401 without a token → ``hf_not_found`` (HF answers 401 for a repository
it will not show); 401 with a token → ``hf_token_rejected`` naming the tier; 403 → ``hf_gated``;
timeout, 5xx and a 200 HTML page → ``hf_unavailable``. The token header is sent only when a token
exists, and the Viewer's failures become ``ViewerUnavailable`` (a preview note, not an error).
"""

from __future__ import annotations

import httpx
import pytest

from src.clients.hf_hub import HfError, ViewerUnavailable, map_hf_error
from tests.support.hf_mock import COLBERT, GATED, HfMock, fixture, hub_client


@pytest.mark.parametrize(
    ("status", "token_sent", "body", "code", "http"),
    [
        (404, False, "", "hf_not_found", 404),
        (404, True, "", "hf_not_found", 404),
        (401, False, "", "hf_not_found", 404),
        (401, True, "", "hf_token_rejected", 401),
        (403, True, "", "hf_gated", 403),
        (403, False, "", "hf_gated", 403),
        (401, True, "access to this repo is gated", "hf_gated", 403),
        (500, False, "", "hf_unavailable", 502),
        (503, True, "", "hf_unavailable", 502),
        (None, False, "", "hf_unavailable", 502),
    ],
)
def test_each_answer_maps_to_its_code(
    status: int | None, token_sent: bool, body: str, code: str, http: int
) -> None:
    error = map_hf_error(status, body, token_sent=token_sent, repo="o/r", tier="per_import")
    assert (error.code, error.status_code) == (code, http)


def test_a_rejected_token_names_its_tier() -> None:
    for tier in ("per_import", "stored"):
        error = map_hf_error(401, "", token_sent=True, repo="o/r", tier=tier)
        assert error.details["tier"] == tier
        assert tier in error.message


def test_every_message_says_what_to_do_next() -> None:
    for status, sent in ((404, False), (401, True), (403, True), (500, False)):
        message = map_hf_error(status, "", token_sent=sent, repo="o/r").message
        assert message.rstrip().endswith(".") and len(message) > 40


def test_a_200_html_page_is_unavailable_not_an_empty_success() -> None:
    hub, _ = hub_client({f"/api/datasets/{COLBERT}": (200, "<html>sign in</html>")})
    with pytest.raises(HfError) as info:
        hub.dataset(COLBERT)
    assert info.value.code == "hf_unavailable"


def test_a_timeout_is_unavailable() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    hub, _ = hub_client({f"/api/datasets/{COLBERT}": slow})
    with pytest.raises(HfError) as info:
        hub.dataset(COLBERT)
    assert info.value.code == "hf_unavailable"


def test_a_5xx_is_unavailable() -> None:
    hub, _ = hub_client({f"/api/datasets/{COLBERT}": (502, {"error": "bad gateway"})})
    with pytest.raises(HfError) as info:
        hub.dataset(COLBERT)
    assert info.value.code == "hf_unavailable"


def test_the_token_header_is_sent_only_when_a_token_exists() -> None:
    hub, seen = hub_client(HfMock())
    hub.dataset(COLBERT)
    assert "authorization" not in seen[-1].headers
    hub2, seen2 = hub_client(HfMock(), token="hf_live_token_x")
    hub2.dataset(COLBERT)
    assert seen2[-1].headers["authorization"] == "Bearer hf_live_token_x"


def test_an_unknown_repository_without_a_token_is_not_found() -> None:
    hub, _ = hub_client(
        {"/api/datasets/nobody-zz/no-such-dataset-zz": (401, fixture("not_found.json"))}
    )
    with pytest.raises(HfError) as info:
        hub.dataset("nobody-zz/no-such-dataset-zz")
    assert info.value.code == "hf_not_found"


def test_an_unknown_repository_with_a_token_is_token_rejected_naming_the_tier() -> None:
    hub, _ = hub_client(
        {"/api/datasets/nobody-zz/private-zz": (401, fixture("not_found.json"))},
        token="hf_some_token",
        tier="per_import",
    )
    with pytest.raises(HfError) as info:
        hub.dataset("nobody-zz/private-zz")
    assert info.value.code == "hf_token_rejected" and info.value.details["tier"] == "per_import"


def test_a_gated_dataset_whose_terms_are_not_accepted_is_gated() -> None:
    hub, _ = hub_client(
        {f"/api/datasets/{GATED}/revision/main": (403, {"error": "Access to this repo is gated"})},
        token="hf_terms_not_accepted",
    )
    with pytest.raises(HfError) as info:
        hub.revision(GATED, "main")
    assert info.value.code == "hf_gated"


def test_a_viewer_failure_is_viewer_unavailable_not_an_error() -> None:
    hub, _ = hub_client(HfMock())
    with pytest.raises(ViewerUnavailable) as info:
        hub.viewer_first_rows(GATED, "default", "train")
    assert info.value.part == "first_rows"
