"""The gate for the real-Hub integration test (008 FTASKS 8.11).

The test writes to huggingface.co, so it runs only when the operator names BOTH a token and a
target namespace, in variables of its own:

- ``MIDATAWORKS_REAL_HUB_TOKEN``: a Hugging Face token with write access to the namespace;
- ``MIDATAWORKS_REAL_HUB_NAMESPACE``: the user or organisation the throwaway repository goes under.

``HF_TOKEN`` is deliberately NOT read: a developer's ambient Hub login must never turn a routine
``pytest tests`` into a write to the Hub. Without both variables the test SKIPS with a reason that
says what to set (``REAL-HUB:``, as the cross-repo checks say ``CROSS-REPO:``), and under
``MIDATAWORKS_REQUIRE_REAL_HUB=1`` it FAILS instead, for a run that must prove the real Hub (the
same shape as ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS``). The token never appears in a message.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import pytest

TOKEN_VAR = "MIDATAWORKS_REAL_HUB_TOKEN"
NAMESPACE_VAR = "MIDATAWORKS_REAL_HUB_NAMESPACE"
REQUIRE_VAR = "MIDATAWORKS_REQUIRE_REAL_HUB"


@dataclass(frozen=True)
class RealHubTarget:
    token: str
    namespace: str

    def __repr__(self) -> str:  # the token must not reach a traceback or a log
        return f"RealHubTarget(token=<redacted>, namespace={self.namespace!r})"


def real_hub_target(env: Mapping[str, str] | None = None) -> RealHubTarget:
    """The configured target, or a loud skip (a failure under ``MIDATAWORKS_REQUIRE_REAL_HUB=1``)."""
    env = os.environ if env is None else env
    token = (env.get(TOKEN_VAR) or "").strip()
    namespace = (env.get(NAMESPACE_VAR) or "").strip().strip("/")
    missing = [
        name for name, value in ((TOKEN_VAR, token), (NAMESPACE_VAR, namespace)) if not value
    ]
    if not missing:
        return RealHubTarget(token, namespace)
    message = (
        f"{' and '.join(missing)} not set: the real-Hub publish test (008 FTASKS 8.11) writes a "
        f"private throwaway repository under {NAMESPACE_VAR} with the write token in {TOKEN_VAR}, "
        "then deletes it. Set both to run it."
    )
    if env.get(REQUIRE_VAR) == "1":
        pytest.fail(f"{REQUIRE_VAR}=1 but {message}")
    pytest.skip("REAL-HUB: " + message)
