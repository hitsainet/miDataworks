"""The Hub client over a stubbed ``HfApi``: exact arguments, retries, and no token anywhere
(FR-008.17, 008.18; FTASKS 8.1, 8.9, 15.3)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from huggingface_hub.errors import HfHubHTTPError

from src.hub.hub_client import HubClient, HubError
from src.services.publishing.hub_plan import BuiltFile, Plan

TOKEN = "hf_SECRETsecretSECRET0123456789"


def _err(status: int, headers: dict[str, str] | None = None) -> HfHubHTTPError:
    request = httpx2.Request(
        "POST", "https://huggingface.co/api", headers={"authorization": f"Bearer {TOKEN}"}
    )
    return HfHubHTTPError(
        f"boom with Bearer {TOKEN}",
        response=httpx2.Response(status, headers=headers or {}, request=request),
    )


class StubApi:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.raise_next: list[BaseException] = []

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        if self.raise_next:
            raise self.raise_next.pop(0)
        return None

    def create_repo(self, *a: Any, **k: Any) -> Any:
        return self._call("create_repo", *a, **k)

    def create_commit(self, *a: Any, **k: Any) -> Any:
        self._call("create_commit", *a, **k)
        return SimpleNamespace(oid="c" * 40)

    def dataset_info(self, *a: Any, **k: Any) -> Any:
        self._call("dataset_info", *a, **k)
        return SimpleNamespace(private=True, sha="h" * 40, siblings=[])

    def update_repo_settings(self, *a: Any, **k: Any) -> Any:
        return self._call("update_repo_settings", *a, **k)

    def whoami(self, *a: Any, **k: Any) -> Any:
        self._call("whoami", *a, **k)
        return {
            "name": "mistudio",
            "orgs": [{"name": "hitsai"}],
            "auth": {"accessToken": {"role": "write"}},
        }


def client(**kw: Any) -> tuple[HubClient, StubApi]:
    holder: dict[str, StubApi] = {}

    def factory(token: str | None = None) -> StubApi:
        holder["api"] = StubApi(token)
        return holder["api"]

    hub = HubClient(TOKEN, api_factory=factory, sleep=lambda s: None, **kw)
    return hub, holder["api"]


def test_writes_use_the_exact_arguments() -> None:
    hub, api = client()
    hub.create_private_repo("mistudio/x")
    plan = Plan(
        adds=(BuiltFile("README.md", __file__, "card", None, 1, "a" * 64, "b" * 40),),
        deletes=("old.parquet",),
        parent_commit="p" * 40,
    )
    assert hub.commit("mistudio/x", plan, "msg [dw-publish pub_1]") == "c" * 40
    hub.set_public("mistudio/x")
    hub.read_state("mistudio/x")
    create, commit, settings, info = api.calls
    assert create == (
        "create_repo",
        ("mistudio/x",),
        {"private": True, "repo_type": "dataset", "exist_ok": False},
    )
    assert commit[2]["parent_commit"] == "p" * 40 and commit[2]["repo_type"] == "dataset"
    ops = commit[1][1]
    assert [type(o).__name__ for o in ops] == ["CommitOperationAdd", "CommitOperationDelete"]
    assert settings[2] == {"private": False, "repo_type": "dataset"}
    assert info[2] == {"files_metadata": True}


def test_the_token_is_never_kept_as_a_string_or_shown() -> None:
    hub, api = client()
    assert api.token == TOKEN
    assert TOKEN not in repr(hub) and TOKEN not in str(hub)
    assert not [v for v in vars(hub).values() if v == TOKEN]


def test_a_hub_error_carries_no_token_header_or_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    hub, api = client(retries=0)
    api.raise_next = [_err(500)]
    with pytest.raises(HubError) as info:
        hub.read_state("mistudio/x")
    error = info.value
    assert TOKEN not in str(error) and TOKEN not in repr(error.as_dict())
    assert error.__cause__ is None and error.__suppress_context__
    assert error.code == "hub_unavailable" and error.status == 500
    assert TOKEN not in caplog.text


def test_a_429_is_retried_with_retry_after_and_the_wait_is_reported() -> None:
    waits: list[float] = []
    reported: list[str] = []
    holder: dict[str, StubApi] = {}

    def factory(token: str | None = None) -> StubApi:
        holder["api"] = StubApi(token)
        return holder["api"]

    hub = HubClient(
        TOKEN, api_factory=factory, sleep=waits.append, on_wait=reported.append, retries=3
    )
    holder["api"].raise_next = [_err(429, {"Retry-After": "7"}), _err(503)]
    assert hub.read_state("mistudio/x").exists
    assert waits == [7.0, 4.0] and len(reported) == 2


def test_a_commit_is_never_retried_by_the_client() -> None:
    hub, api = client(retries=5)
    api.raise_next = [_err(503)]
    with pytest.raises(HubError) as info:
        hub.commit("mistudio/x", Plan((), (), None), "m")
    assert info.value.code == "hub_unavailable"
    assert [c[0] for c in api.calls] == ["create_commit"]


def test_a_rejected_parent_commit_is_repo_head_moved() -> None:
    hub, api = client()
    api.raise_next = [_err(412)]
    with pytest.raises(HubError) as info:
        hub.commit("mistudio/x", Plan((), (), "p" * 40), "m")
    assert info.value.code == "repo_head_moved"


@pytest.mark.parametrize(
    ("info", "repo", "scope"),
    [
        ({"name": "mistudio", "auth": {"accessToken": {"role": "write"}}}, "mistudio/x", "write"),
        (
            {
                "name": "me",
                "orgs": [{"name": "mistudio"}],
                "auth": {"accessToken": {"role": "write"}},
            },
            "mistudio/x",
            "write",
        ),
        (
            {"name": "me", "auth": {"accessToken": {"role": "write"}}},
            "mistudio/x",
            "namespace_denied",
        ),
        ({"name": "mistudio", "auth": {"accessToken": {"role": "read"}}}, "mistudio/x", "read"),
        (
            {
                "name": "me",
                "auth": {
                    "accessToken": {
                        "role": "fineGrained",
                        "fineGrained": {
                            "scoped": [
                                {
                                    "entity": {"type": "user", "name": "mistudio"},
                                    "permissions": ["repo.content.read", "repo.write"],
                                }
                            ]
                        },
                    }
                },
            },
            "mistudio/x",
            "write",
        ),
        (
            {
                "name": "me",
                "auth": {
                    "accessToken": {
                        "role": "fineGrained",
                        "fineGrained": {
                            "scoped": [
                                {
                                    "entity": {"type": "user", "name": "mistudio"},
                                    "permissions": ["repo.content.read"],
                                }
                            ]
                        },
                    }
                },
            },
            "mistudio/x",
            "namespace_denied",
        ),
        (
            {"name": "me", "auth": {"accessToken": {"role": "fineGrained"}}},
            "mistudio/x",
            "cannot_verify",
        ),
        (
            {"name": "me", "auth": {"accessToken": {"role": "something-new"}}},
            "mistudio/x",
            "cannot_verify",
        ),
        ({"name": "me"}, "mistudio/x", "cannot_verify"),
    ],
)
def test_whoami_scope_fails_closed(info: dict[str, Any], repo: str, scope: str) -> None:
    hub, api = client()
    api.whoami = lambda *a, **k: info  # type: ignore[method-assign]
    assert hub.whoami_scope(repo) == scope


def test_a_rejected_token_is_invalid() -> None:
    hub, api = client(retries=0)
    api.raise_next = [_err(401)]
    assert hub.whoami_scope("mistudio/x") == "invalid"
