"""Every request-time error code of feature 001 answers in the one envelope (001 FTASKS 10.4).

Each code is driven through the real route (HF answers recorded or forced through ``hf_env``), and
each answer is checked for its HTTP status (001 FTDD 5.2), the ``{"error": {code, message,
details}}`` shape, a message that names a next step, and no library text: no exception class, no
URL of the upstream call, no traceback. Worker-time codes (``remote_code_refused``,
``insufficient_space``, ``upload_malformed``, ...) are stored on the job in the same envelope and are
asserted by the job tests that produce them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from tests.support import db_factories
from tests.support.hf_mock import COLBERT, HUMICROEDIT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
PREVIEW = "/api/v1/sources/hf/preview"
LEAKS = ("Traceback", "httpx", "HTTPStatusError", "Exception", "huggingface.co/api", 'File "')


def force(route: Any) -> Callable[[HfEnv], None]:
    def apply(env: HfEnv) -> None:
        env.mock.overrides["/api/datasets/org/forced"] = route

    return apply


def timeout(env: HfEnv) -> None:
    env.preview_timeout = True


CASES: list[tuple[str, dict[str, Any], Callable[[HfEnv], None] | None, int]] = [
    ("repo_id_invalid", {"repo_id": "not a repo"}, None, 422),
    ("hf_not_found", {"repo_id": "org/missing"}, None, 404),
    ("hf_gated", {"repo_id": "org/forced"}, force((403, {"error": "gated"})), 403),
    ("hf_unavailable", {"repo_id": "org/forced"}, force((503, {"error": "down"})), 502),
    ("hf_unavailable", {"repo_id": "org/forced"}, force((200, "<html>maintenance</html>")), 502),
    ("revision_unresolved", {"repo_id": COLBERT, "revision": "no-such-tag"}, None, 404),
    ("config_required", {"repo_id": HUMICROEDIT}, None, 409),
    ("config_not_found", {"repo_id": COLBERT, "config": "nope"}, None, 409),
    ("split_not_found", {"repo_id": COLBERT, "split": "nope"}, None, 409),
    ("preview_timeout", {"repo_id": COLBERT}, timeout, 504),
]


def assert_envelope(response: httpx.Response, code: str, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message", "details"}
    error: dict[str, Any] = body["error"]
    assert error["code"] == code
    assert len(error["message"]) > 20 and error["message"].rstrip().endswith((".", ")"))
    for leak in LEAKS:
        assert leak not in response.text, (code, leak)
    return error


@pytest.mark.parametrize(
    ("code", "request_body", "setup", "status"),
    CASES,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)],
)
async def test_each_preview_code_has_its_status_and_the_envelope(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    code: str,
    request_body: dict[str, Any],
    setup: Callable[[HfEnv], None] | None,
    status: int,
) -> None:
    if setup:
        setup(hf_env)
    error = assert_envelope(await client.post(PREVIEW, json=request_body), code, status)
    if code in ("config_required", "config_not_found"):
        assert error["details"]["configs"], "the valid choices are listed"
    if code == "split_not_found":
        assert error["details"]["splits"]


async def test_a_malformed_repo_id_is_refused_before_any_network_call(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    for path in (PREVIEW, "/api/v1/sources/hf"):
        assert_envelope(await client.post(path, json={"repo_id": "../etc"}), "repo_id_invalid", 422)
    assert hf_env.mock.seen == [] and hf_env.sent == []


async def test_upload_manifest_invalid(client: httpx.AsyncClient, operator_name: str) -> None:
    response = await client.post(
        "/api/v1/sources/uploads",
        files={"files": ("a.jsonl", b'{"text": "x"}\n')},
        data={"manifest": '{"files": []}'},
    )
    assert_envelope(response, "upload_manifest_invalid", 422)


@pytest.mark.parametrize(
    ("state", "code"), [("importing", "source_importing"), ("ready", "source_in_use")]
)
async def test_delete_conflicts(
    client: httpx.AsyncClient, operator_name: str, state: str, code: str
) -> None:
    from src.core.database import sync_session_factory

    with sync_session_factory()() as s:
        source = db_factories.source(s, state=state)
        if code == "source_in_use":
            db_factories.version_reading(s, source)
        s.commit()
        source_id = source.id
    response = await client.request(
        "DELETE", f"/api/v1/sources/{source_id}", json={"reason": "cleanup"}
    )
    error = assert_envelope(response, code, 409)
    if code == "source_in_use":
        assert error["details"]["versions"]


async def test_annotating_a_deleted_source(client: httpx.AsyncClient, operator_name: str) -> None:
    from src.core.database import sync_session_factory

    with sync_session_factory()() as s:
        source = db_factories.source(s, state="deleted")
        s.commit()
        source_id = source.id
    response = await client.post(
        f"/api/v1/sources/{source_id}/annotations",
        json={"kind": "terms", "redistribution": "permits", "reason": "read"},
    )
    assert_envelope(response, "source_deleted", 409)


async def test_rows_of_a_source_that_is_not_ready(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    from src.core.database import sync_session_factory

    with sync_session_factory()() as s:
        source = db_factories.source(s, state="importing")
        s.commit()
        source_id = source.id
    assert_envelope(await client.get(f"/api/v1/sources/{source_id}/rows"), "source_not_ready", 409)
    missing = await client.get("/api/v1/sources/00000000-0000-0000-0000-000000000000")
    assert_envelope(missing, "source_not_found", 404)
