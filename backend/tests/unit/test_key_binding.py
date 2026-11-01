"""A role key goes only to the origin it was entered for (operator decision 2026-10-20).

- ``url_origin``: scheme, host and port, normalised; the relay's and migration 0031's frozen copies
  give the same answers (the designer image and a migration cannot import ``src.core``).
- ``EndpointCaller``: every request method sends the key to the bound origin and to no other.
- ``fetch_models``: the same rule on its own client.
- Wiring: every ``EndpointCaller(...)`` / ``caller_for(...)`` call in ``src`` that passes a key
  also passes ``key_origin`` (a call that forgets it would silently stop sending the key).
- Logs: the two ``key_withheld`` warnings name origins only, never the key or anything else in the
  URL (credentials in its userinfo, a token in its query) — mirror CodeQL alert #10 reviewed
  2026-10-20.
"""

from __future__ import annotations

import ast
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.clients import endpoint_caller
from src.clients.endpoint_caller import EndpointCaller
from src.clients.model_listing import fetch_models
from src.core.key_binding import key_for, same_origin, url_origin
from src.operators.data_designer import relay

SRC = Path(__file__).resolve().parents[2] / "src"
MIGRATION = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0031_endpoint_key_origin.py"
)
KEY = "sk-binding-key-0123456789"

ORIGINS: list[tuple[str | None, str | None]] = [
    ("http://millm.test/v1", "http://millm.test:80"),
    ("http://millm.test:80/v1", "http://millm.test:80"),
    ("HTTP://MiLLM.Test/v1/", "http://millm.test:80"),
    ("https://api.example.com/v1/systemone", "https://api.example.com:443"),
    ("https://api.example.com:8443", "https://api.example.com:8443"),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000"),
    ("http://[::1]:9000/v1", "http://[::1]:9000"),
    ("http://user:pw@host.test/v1", "http://host.test:80"),
    ("  http://millm.test/v1  ", "http://millm.test:80"),
    ("millm.test/v1", None),
    ("ftp://files.test/x", None),
    ("http://bad.test:notaport/v1", None),
    ("", None),
    (None, None),
]


def _migration_origin() -> Any:
    spec = importlib.util.spec_from_file_location("migration_0031", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.origin_of


@pytest.mark.parametrize(("url", "origin"), ORIGINS)
def test_url_origin_and_its_two_copies_agree(url: str | None, origin: str | None) -> None:
    assert url_origin(url) == origin
    assert relay.url_origin(url) == origin, "the relay's copy drifted"
    assert _migration_origin()(url) == origin, "migration 0031's copy drifted"


def test_key_for() -> None:
    bound = "http://millm.test:80"
    assert key_for("http://millm.test/v1", KEY, bound) == KEY
    assert key_for("http://millm.test/other", KEY, bound) == KEY, "same origin, other path"
    assert key_for("https://millm.test/v1", KEY, bound) is None, "scheme"
    assert key_for("http://millm.test:8080/v1", KEY, bound) is None, "port"
    assert key_for("http://evil.test/v1", KEY, bound) is None, "host"
    assert key_for("http://millm.test/v1", KEY, None) is None, "a key bound to nothing"
    assert key_for(None, KEY, bound) is None
    assert key_for("http://millm.test/v1", None, bound) is None
    assert same_origin("http://a.test/v1", "HTTP://A.TEST:80/x")
    assert not same_origin(None, None)


# --- EndpointCaller -----------------------------------------------------------------------------


@pytest.fixture
def seen() -> Any:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    previous = endpoint_caller.install_transport(lambda origin: httpx.MockTransport(handler))
    yield requests
    endpoint_caller.install_transport(previous)


def _every_method(caller: EndpointCaller) -> None:
    caller.call("POST", "/v1/completions", body={}, purpose="scoring", openai=True)
    caller.raw("GET", "/v1/models")
    caller.raw_bytes("GET", "/v1/files/x/content")
    caller.upload("/v1/files", filename="a.jsonl", content=b"{}", fields={"purpose": "batch"})


@pytest.mark.parametrize(
    ("base_url", "key_origin", "sent"),
    [
        ("http://millm.test/v1", "http://millm.test:80", True),
        ("http://millm.test:80/v1/", "http://millm.test:80", True),
        ("http://millm.test/v1", "http://evil.test:80", False),
        ("https://millm.test/v1", "http://millm.test:80", False),
        ("http://millm.test:8000/v1", "http://millm.test:80", False),
        ("http://millm.test/v1", None, False),
    ],
)
def test_the_caller_sends_the_key_only_to_its_origin(
    seen: list[httpx.Request], base_url: str, key_origin: str | None, sent: bool
) -> None:
    with EndpointCaller(base_url, KEY, key_origin=key_origin, sleep=lambda s: None) as caller:
        _every_method(caller)
    assert len(seen) == 4
    expected = f"Bearer {KEY}" if sent else None
    assert [r.headers.get("authorization") for r in seen] == [expected] * 4


async def test_fetch_models_sends_the_key_only_to_its_origin() -> None:
    seen: list[httpx.Request] = []

    class Async(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": [{"id": "m"}]})

    await fetch_models("http://m.test/v1", KEY, key_origin="http://m.test:80", transport=Async())
    await fetch_models("http://m.test/v1", KEY, key_origin="http://x.test:80", transport=Async())
    await fetch_models("http://m.test/v1", KEY, transport=Async())
    assert [r.headers.get("authorization") for r in seen] == [f"Bearer {KEY}", None, None]


# --- wiring: every keyed caller passes the key's origin -----------------------------------------


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _keyed_calls() -> list[tuple[str, int, bool]]:
    """(file, line, passes key_origin) for every caller construction given a possible key."""
    found: list[tuple[str, int, bool]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # The two ``caller_for`` factories forward **overrides (key_origin among them).
        exempt = {
            id(node)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef) and fn.name == "caller_for"
            for node in ast.walk(fn)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in exempt:
                continue
            if _name(node.func) not in ("EndpointCaller", "caller_for"):
                continue
            key_arg = node.args[1] if len(node.args) > 1 else None
            if key_arg is None or (isinstance(key_arg, ast.Constant) and key_arg.value is None):
                continue  # no key: nothing to bind
            passes = any(k.arg == "key_origin" for k in node.keywords)
            found.append((str(path.relative_to(SRC)), node.lineno, passes))
    return found


def test_every_keyed_caller_passes_the_key_origin() -> None:
    calls = _keyed_calls()
    # Counted 2026-10-20; a lower count means the scan stopped seeing the call sites.
    assert len(calls) >= 15, calls
    missing = [(f, line) for f, line, passes in calls if not passes]
    assert missing == [], f"these callers pass a key without its origin: {missing}"


# --- Logs carry origins, never key material (CodeQL #10 review, 2026-10-20) ----------------------

#: Secret-looking parts a base URL might carry; none may reach a log line.
URL_PW = "url-userinfo-password-7f3a"
URL_TOKEN = "url-query-token-91c2"
LEAKY_URL = f"http://user:{URL_PW}@evil.test:8080/v1?api_key={URL_TOKEN}"
SECRETS = (KEY, KEY[-8:], URL_PW, URL_TOKEN)


def _assert_withheld_log_is_clean(caplog: pytest.LogCaptureFixture, event: str) -> None:
    records = [r for r in caplog.records if event in r.getMessage()]
    assert len(records) == 1, "the withheld warning must be logged (else this test proves nothing)"
    line = records[0].getMessage()
    assert "http://evil.test:8080" in line and "http://millm.test:80" in line, line
    for text in (line, caplog.text):
        leaked = [s for s in SECRETS if s in text]
        assert leaked == [], f"key material in a log: {leaked}"


def test_key_for_logs_origins_never_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    assert key_for(LEAKY_URL, KEY, "http://millm.test:80") is None
    _assert_withheld_log_is_clean(caplog, "endpoint.key_withheld")


def test_the_relay_logs_origins_never_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    endpoint = SimpleNamespace(
        base_url=LEAKY_URL,
        model="m",
        api_key=KEY,
        is_millm=False,
        key_origin="http://millm.test:80",
    )
    built = relay.LoopbackRelay(endpoint)
    assert built._key is None
    _assert_withheld_log_is_clean(caplog, "relay.key_withheld")
