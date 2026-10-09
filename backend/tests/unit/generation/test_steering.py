"""``X-miLLM-Steering`` parsing and the reported-state check, on a header corpus (FTASKS 3.5, 3.6).

The header values are written the way miLLM's ``serialize_steering_header`` writes them (miLLM
``millm/core/steering_state.py`` and its tests): RFC 8941 list members, parameters in the FTDD 5.2
order, ``intensity`` a String of ``repr(float)``, ``name`` percent-encoded, a true boolean bare.
Every mismatch names its reason, and a missing header and ``unknown`` are both UNREPORTED — the
check is on the member's kind token, never an equality on the whole value.
"""

from __future__ import annotations

import pytest

from src.services.generation import steering

SAE = "LiquidAI--LFM2.5-1.2B-Instruct-sae--layer_11"
HASH = steering.applied_set_hash(SAE, [(1234, 8.0)])


def inline_expected() -> steering.Expected:
    return steering.Expected("inline", sae_id=SAE, layer=11, features=((1234, 8.0),), set_hash=HASH)


def profile_expected(name: str = "humor", intensity: float = 1.0) -> steering.Expected:
    return steering.Expected(
        "profile",
        profile_name=name,
        intensity=intensity,
        sae_id=SAE,
        layer=11,
        features=((1234, 8.0),),
        set_hash=HASH,
    )


def profile_header(
    *, name: str = "humor", source: str = "request", intensity: str = "1.0", extra: str = ""
) -> str:
    return (
        f'profile;name="{name}";source={source};intensity="{intensity}";sae="{SAE}";layer=11;'
        f'features=1;hash="{HASH}"{extra}'
    )


def inline_header(*, features: int = 1, digest: str = HASH, extra: str = "") -> str:
    return f'inline;sae="{SAE}";layer=11;features={features};hash="{digest}"{extra}'


NONE = steering.Expected("none")


def check(expected: steering.Expected | None, header: str | None) -> steering.Check:
    return steering.check_header(expected, header)


def test_none_matches_an_unsteered_side() -> None:
    assert check(NONE, "none").result == "match"


def test_profile_from_the_request_matches() -> None:
    assert check(profile_expected(), profile_header()).result == "match"


def test_profile_applied_as_the_active_one_is_a_source_mismatch() -> None:
    result = check(profile_expected(), profile_header(source="active"))
    assert result.result == "mismatch" and "source" in result.reasons


def test_inline_match() -> None:
    assert check(inline_expected(), inline_header()).result == "match"


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        (inline_header(digest="sha256:" + "0" * 64), "hash"),
        (inline_header(features=2), "features"),
        (inline_header().replace(f'sae="{SAE}"', 'sae="other"'), "sae"),
        (inline_header().replace("layer=11", "layer=12"), "layer"),
    ],
)
def test_inline_field_mismatches_name_their_reason(header: str, reason: str) -> None:
    result = check(inline_expected(), header)
    assert result.result == "mismatch" and reason in result.reasons
    assert result.reason_code() == "steering_mismatch"


def test_intensity_mismatch() -> None:
    result = check(profile_expected(intensity=0.5), profile_header(intensity="1.0"))
    assert result.result == "mismatch" and "intensity" in result.reasons


def test_intensity_is_compared_as_a_float_not_as_text() -> None:
    """``"1.0"`` equals an expected 1 (an int, as a JSON profile may hold it)."""
    assert check(profile_expected(intensity=1), profile_header(intensity="1.0")).result == "match"


def test_changed_is_a_mismatch_even_when_everything_else_matches() -> None:
    result = check(inline_expected(), inline_header(extra=";changed"))
    assert result.result == "mismatch" and result.reasons == ("changed",)


def test_none_with_changed_is_a_mismatch() -> None:
    result = check(NONE, "none;changed")
    assert result.result == "mismatch" and "changed" in result.reasons


def test_a_clamped_count_is_a_mismatch() -> None:
    result = check(inline_expected(), inline_header(extra=";clamped=1"))
    assert result.result == "mismatch" and "clamped" in result.reasons


def test_extra_items_are_a_mismatch() -> None:
    header = inline_header() + ', manual;sae="x";layer=3;features=1;hash="sha256:m"'
    result = check(inline_expected(), header)
    assert result.result == "mismatch" and result.reasons == ("extra_items",)


def test_unknown_is_unreported() -> None:
    result = check(inline_expected(), "unknown")
    assert result.result == "unreported" and result.reason_code() == "steering_unreported"


def test_unknown_with_a_reason_is_unreported_by_kind_not_by_value() -> None:
    """miLLM's own fallback is ``unknown;reason=read_failed``: equality on the bare word would miss
    it and call the response a mismatch or, worse, a match."""
    assert check(inline_expected(), "unknown;reason=read_failed").result == "unreported"
    assert check(NONE, "unknown;reason=claims_unreadable;changed").result == "unreported"


def test_a_missing_header_is_unreported_never_unsteered() -> None:
    result = check(NONE, None)
    assert result.result == "unreported" and result.reasons == ("missing",)


def test_a_non_millm_endpoint_is_not_applicable() -> None:
    assert check(None, None).result == "not_applicable"


def test_unsteered_side_reported_as_an_active_profile_is_a_kind_mismatch() -> None:
    """A globally active profile steering an 'unsteered' request (FPRD 007 section 12.6)."""
    result = check(NONE, profile_header(source="active"))
    assert result.result == "mismatch" and "kind" in result.reasons


def test_a_percent_encoded_name_decodes_before_comparison() -> None:
    expected = profile_expected(name='café "x"')
    header = profile_header(name="caf%C3%A9 %22x%22")
    assert check(expected, header).result == "match"
    parsed = steering.parse_steering_header(header)
    assert parsed.items[0].params["name"] == 'café "x"'


def test_a_malformed_header_is_a_mismatch() -> None:
    result = check(NONE, "profile;name=")
    assert result.result == "mismatch" and "malformed" in result.reasons


def test_the_stream_field_carries_the_same_value() -> None:
    """miLLM FR-28.3.9: a stream's terminal chunk carries the header value as ``millm_steering``."""
    chunk = {"object": "chat.completion.chunk", "choices": [], steering.STREAM_FIELD: "none"}
    assert check(NONE, chunk[steering.STREAM_FIELD]).result == "match"


def test_expected_from_a_snapshot_round_trips() -> None:
    expected = steering.expected_from(
        "inline",
        profile_name=None,
        intensity=None,
        sae_id=SAE,
        layer=11,
        features=[[1234, 8.0]],
        set_hash=HASH,
    )
    assert check(expected, inline_header()).matched
