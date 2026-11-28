"""Hugging Face, answered from recorded bodies, and a fake ``load_dataset`` (001 FTID section 8).

The real client code runs; only the network is replaced (``httpx.MockTransport``). Two shapes:

- :class:`HfMock` routes like the Hub and the Dataset Viewer for the recorded repositories (ColBERT,
  Humicroedit, offensive-humor, a gated one, a scripted one) and records every request. Its
  ``head`` can differ from the pinned commit, its viewer can go down, and a gated repository
  answers 403 to a token whose owner has not accepted the terms.
- :func:`hub_client` takes a dict of exact paths to ``(status, body)`` or a handler, for one-off
  answers (a 200 HTML page, a timeout, a 401).

Fixture honesty (001 FTASKS 14.5): the default head is NOT the pinned commit when a test asks for
it, the tokens are non-empty, and the multi-config repository really has two configs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "hf_fixtures"
COLBERT = "CreativeLang/ColBERT_Humor_Detection"
COLBERT_SHA = "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac"
COMMIT = COLBERT_SHA
HEAD_SHA = "9" * 40
HUMICROEDIT = "tasksource/humicroedit"
HUMICROEDIT_SHA = "f5a16e65b0854032ab9b4c82ca182a6381b83bd5"
OFFENSIVE = "tasksource/offensive-humor"
GATED = "lmsys/lmsys-chat-1m"
SCRIPTED = "org/scripted"

Route = tuple[int, Any] | Callable[[httpx.Request], httpx.Response]


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _json(body: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _answer(route: Route, request: httpx.Request) -> httpx.Response:
    if callable(route):
        return route(request)
    status, body = route
    if isinstance(body, str):
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})
    return _json(body, status)


class HfMock:
    """Routes by path. ``seen`` records every request for assertions."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.overrides: dict[str, Route] = {}
        self.head = COLBERT_SHA
        self.viewer_down = False

    @property
    def auth_headers(self) -> list[str | None]:
        return [r.headers.get("authorization") for r in self.seen]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        for marker, route in self.overrides.items():
            if marker in str(request.url):
                return _answer(route, request)
        path = request.url.path
        if path.startswith("/api/datasets/"):
            if request.url.params.get("blobs") == "true" and path.startswith(
                f"/api/datasets/{HUMICROEDIT}/"
            ):
                return _json(fixture("humicroedit_revision_blobs.json"))
            return self._hub(path, request.headers.get("authorization"))
        if self.viewer_down:
            return _json({"error": "The dataset viewer is not available for this dataset."}, 500)
        return self._viewer(path, dict(request.url.params))

    def _hub(self, path: str, auth: str | None) -> httpx.Response:
        rest = path[len("/api/datasets/") :]
        repo, ref = rest.split("/revision/") if "/revision/" in rest else (rest, "")
        if repo == COLBERT:
            if ref in ("", "main", "2bb7d6bc", COLBERT_SHA, "v1.0"):
                body = fixture("colbert_revision_short.json")
                return _json(dict(body, sha=self.head) if ref in ("", "main") else body)
            return _json(fixture("missing_revision.json"), 404)
        if repo == HUMICROEDIT:
            return _json(fixture("humicroedit_revision.json"))
        if repo == OFFENSIVE:
            return _json(fixture("offensive_revision.json"))
        if repo == GATED:
            return _json(fixture("gated_dataset.json"))
        if repo == SCRIPTED:
            return _json(
                {
                    "sha": "1" * 40,
                    "siblings": [{"rfilename": "scripted.py"}, {"rfilename": "README.md"}],
                    "tags": [],
                }
            )
        return _json(fixture("not_found.json"), 401)

    def _viewer(self, path: str, params: dict[str, str]) -> httpx.Response:
        dataset = params.get("dataset")
        if dataset == HUMICROEDIT and path == "/splits":
            return _json(fixture("humicroedit_splits.json"))
        if dataset == GATED:
            return _json(fixture("gated_viewer_401.json"), 401)
        if dataset != COLBERT:
            return _json({"error": "not available"}, 404)
        if path == "/splits":
            return _json(fixture("colbert_splits.json"))
        if path == "/size":
            name = "colbert_size_config.json" if params.get("config") else "colbert_size.json"
            return _json(fixture(name))
        if path == "/first-rows":
            return _json(fixture("colbert_first_rows.json"))
        return _json({"error": "unknown"}, 404)


def colbert_routes() -> HfMock:
    """A mock that answers every recorded repository (ColBERT's head equals its pin)."""
    return HfMock()


def hub_client(
    routes: dict[str, Route] | HfMock, *, token: str | None = None, tier: str = "stored"
) -> tuple[Any, list[httpx.Request]]:
    """A real ``HubClient`` over a mock transport, and the list of requests it sent."""
    from src.clients.hf_hub import HubClient

    if isinstance(routes, HfMock):
        mock = routes
        seen = mock.seen
        handler: Callable[[httpx.Request], httpx.Response] = mock
    else:
        seen = []
        table = dict(routes)

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            route = table.get(request.url.path)
            if route is None:
                return _json({"error": "no such route in this test"}, 404)
            return _answer(route, request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return HubClient(token, tier=tier, http=http), seen


def client_factory(mock: HfMock) -> Callable[[str | None, str], Any]:
    """What the worker's ``_client`` is replaced with in integration tests."""
    from src.clients.hf_hub import HubClient

    def make(token: str | None, tier: str) -> HubClient:
        return HubClient(token, tier=tier, http=httpx.Client(transport=httpx.MockTransport(mock)))

    return make


class FakeLoader:
    """Implements ``hf_materialise.Loader`` with in-memory splits; records every call's arguments.

    ``slow`` spins in short Python steps for up to 30 s so the watchdog can interrupt it; while it
    spins it writes bytes into ``cache_dir`` so the heartbeat has something to measure.
    """

    def __init__(
        self,
        splits: dict[str, list[dict[str, Any]]] | None = None,
        *,
        slow: bool = False,
        raises: BaseException | None = None,
    ) -> None:
        self.splits = splits or {
            "train": [
                {"text": f"joke number {i} walks into a bar", "humor": i % 2 == 0}
                for i in range(30)
            ],
            "validation": [{"text": "a headline about rates rising", "humor": False}] * 5,
            "test": [{"text": f"another one-liner, take {i}", "humor": True} for i in range(7)],
        }
        self.slow = slow
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, path: str, **kwargs: Any) -> Any:
        import datasets

        self.calls.append({"path": path, **kwargs})
        if self.raises is not None:
            raise self.raises
        if self.slow:
            cache = Path(kwargs["cache_dir"])
            cache.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + 30
            n = 0
            while time.monotonic() < deadline:  # short Python steps the watchdog can interrupt
                (cache / f"chunk_{n % 50}.bin").write_bytes(b"x" * 1024)
                n += 1
                time.sleep(0.01)
        built = {name: datasets.Dataset.from_list(rows) for name, rows in self.splits.items()}
        if kwargs.get("split"):
            return built[kwargs["split"]]
        return datasets.DatasetDict(built)
