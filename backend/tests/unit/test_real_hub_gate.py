"""The real-Hub test's gate skips loudly, fails under REQUIRE, and never prints the token (8.11)."""

from __future__ import annotations

import pytest

from tests.support.real_hub import (
    NAMESPACE_VAR,
    REQUIRE_VAR,
    TOKEN_VAR,
    RealHubTarget,
    real_hub_target,
)

SECRET = "hf_gateTestToken0123456789abcdef"


def test_no_environment_skips_naming_both_variables() -> None:
    with pytest.raises(pytest.skip.Exception) as info:
        real_hub_target({})
    message = str(info.value)
    assert message.startswith("REAL-HUB: ")
    assert TOKEN_VAR in message and NAMESPACE_VAR in message


def test_a_token_without_a_namespace_skips_naming_only_the_namespace() -> None:
    with pytest.raises(pytest.skip.Exception) as info:
        real_hub_target({TOKEN_VAR: SECRET})
    message = str(info.value)
    assert NAMESPACE_VAR in message and f"{TOKEN_VAR} and" not in message
    assert SECRET not in message


def test_the_ambient_hf_token_is_not_read() -> None:
    with pytest.raises(pytest.skip.Exception):
        real_hub_target({"HF_TOKEN": SECRET, NAMESPACE_VAR: "someone"})


@pytest.mark.parametrize(
    "env",
    [{}, {NAMESPACE_VAR: "someone"}, {TOKEN_VAR: SECRET}, {TOKEN_VAR: "  ", NAMESPACE_VAR: "x"}],
)
def test_require_turns_every_missing_case_into_a_failure(env: dict[str, str]) -> None:
    with pytest.raises(pytest.fail.Exception) as info:
        real_hub_target({**env, REQUIRE_VAR: "1"})
    assert REQUIRE_VAR in str(info.value)
    assert SECRET not in str(info.value)


def test_both_set_returns_the_target_and_its_repr_hides_the_token() -> None:
    target = real_hub_target({TOKEN_VAR: SECRET, NAMESPACE_VAR: "/someone/", REQUIRE_VAR: "1"})
    assert target == RealHubTarget(SECRET, "someone")
    assert SECRET not in repr(target)
