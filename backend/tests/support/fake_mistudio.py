"""A fake miStudio for 009's tests (FTASKS 5.5; FTDD 009 section 10).

An ``httpx`` transport serving the seven routes 009 calls, with bodies shaped like the recorded
fixtures in ``tests/fixtures/detector_sets/`` (captured from the live k8s miStudio, 2026-10-07):

- records every request (method, path, query, headers, JSON body) and counts calls per route;
- refuses unknown body keys with ``422``, as ``ProbeDatasetCreate``'s ``extra="forbid"`` does, and
  refuses ``access_token`` on a download the same way miStudio would accept it — so a test can see
  it arrived (it must never arrive);
- computes a view's ``counts`` from the label values the test seeded for that (repository, split),
  under the body's mapping, exactly as miStudio does (a mismatch is scripted, never accidental);
- scripted failures per route: a status with a body, a ``200`` that is not JSON, a dropped
  connection, or a download that ends in ``error``.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "detector_sets"
BASE = "http://mistudio.test"

REGISTRATION_FIELDS = {
    "name",
    "dataset_id",
    "config",
    "split",
    "input_column",
    "label_column",
    "label_mapping",
    "keyword_filter",
    "pair_column",
    "role",
    "distribution",
}
DOWNLOAD_FIELDS = {"repo_id", "access_token", "split", "config"}


@dataclass
class Captured:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: Any


@dataclass
class FakeMiStudio:
    #: (repo_id, split) -> {label value: rows}; what a downloaded split holds.
    values: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)
    #: polls of GET /datasets/{id} before it reports ready.
    polls_until_ready: int = 1
    requests: list[Captured] = field(default_factory=list)
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    views: dict[str, dict[str, Any]] = field(default_factory=dict)
    runs: list[dict[str, Any]] = field(default_factory=list)
    probes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: run id -> ``GET /probe-monitors/runs/{id}`` body (``run_pmr_a1993af56691.json``'s shape).
    run_rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: dataset id -> the rows ``GET /datasets/{id}/samples`` serves, in order (each row's ``data``;
    #: ``samples_humicroedit_eval_page1.json``'s shape). Paged exactly as miStudio pages them.
    samples: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    openapi_doc: dict[str, Any] = field(default_factory=dict)
    #: route key -> list of scripted responses consumed in order ("METHOD /path-template").
    script: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: (repo, split) -> counts override (a stale or wrong split).
    wrong_counts: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)
    #: download ids whose status ends in error, with miStudio's message.
    download_errors: dict[tuple[str, str], str] = field(default_factory=dict)
    _polls: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if not self.openapi_doc:
            self.openapi_doc = json.loads((FIXTURES / "mistudio_openapi.json").read_text())

    # --- helpers for tests ---------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str, prefix: str) -> list[Captured]:
        return [r for r in self.requests if r.method == method and r.path.startswith(prefix)]

    def count(self, method: str, prefix: str) -> int:
        return len(self.calls(method, prefix))

    def fail(self, key: str, **response: Any) -> None:
        self.script.setdefault(key, []).append(response)

    def seed_existing_dataset(self, repo_id: str, split: str) -> str:
        dataset_id = str(uuid.uuid4())
        self.datasets[dataset_id] = self._dataset(dataset_id, repo_id, split, "ready")
        return dataset_id

    # --- the transport -------------------------------------------------------------------------

    def _dataset(self, dataset_id: str, repo_id: str, split: str, status: str) -> dict[str, Any]:
        return {
            "id": dataset_id,
            "name": repo_id.split("/")[-1],
            "source": "HuggingFace",
            "hf_repo_id": repo_id,
            "status": status,
            "progress": 100.0 if status == "ready" else 10.0,
            "error_message": None,
            "raw_path": f"/data/datasets/{repo_id.replace('/', '_')}",
            "num_samples": None,
            "size_bytes": None,
            "metadata": {"split": split, "config": None, "access_token_provided": False},
            "created_at": "2026-10-07T00:00:00Z",
            "updated_at": "2026-10-07T00:00:00Z",
            "tokenizations": [],
        }

    def _scripted(self, key: str) -> httpx.Response | None:
        queue = self.script.get(key)
        if not queue:
            return None
        item = queue.pop(0)
        if item.get("drop"):
            raise httpx.ConnectError("connection dropped (scripted)")
        if item.get("non_json"):
            return httpx.Response(
                200, text="<html>not miStudio</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(int(item["status"]), json=item.get("body", {}))

    def handle(self, request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode("utf-8", "replace")
        path = request.url.path
        self.requests.append(
            Captured(
                request.method,
                path,
                dict(request.url.params),
                {k.lower(): v for k, v in request.headers.items()},
                body,
            )
        )
        parts = path.strip("/").split("/")
        method = request.method
        if method == "POST" and path == "/api/v1/datasets/download":
            return self._scripted("POST /datasets/download") or self._download(body)
        if method == "GET" and path == "/api/v1/datasets":
            return self._scripted("GET /datasets") or httpx.Response(
                200, json={"data": list(self.datasets.values()), "pagination": {}}
            )
        if (
            method == "GET"
            and len(parts) == 5
            and parts[:3] == ["api", "v1", "datasets"]
            and parts[4] == "samples"
        ):
            return self._scripted("GET /datasets/{id}/samples") or self._samples(
                parts[3], request.url.params
            )
        if (
            method == "GET"
            and len(parts) == 5
            and parts[:4] == ["api", "v1", "probe-monitors", "runs"]
        ):
            found = self.run_rows.get(parts[4])
            if found is None:
                # miStudio c829a2cc's body, captured 2026-10-07.
                return httpx.Response(
                    404, json={"detail": f"Probe monitor run {parts[4]} not found"}
                )
            return httpx.Response(200, json=found)
        if method == "GET" and len(parts) == 4 and parts[:3] == ["api", "v1", "datasets"]:
            return self._scripted("GET /datasets/{id}") or self._get_dataset(parts[3])
        if method == "POST" and path == "/api/v1/probe-monitors/datasets":
            return self._scripted("POST /probe-monitors/datasets") or self._register(body)
        if method == "GET" and path == "/api/v1/probe-monitors/datasets":
            return httpx.Response(200, json=list(self.views.values()))
        if method == "GET" and path == "/api/v1/probe-monitors/runs":
            return self._scripted("GET /probe-monitors/runs") or httpx.Response(200, json=self.runs)
        if method == "GET" and path == "/api/v1/probe-monitors/probes":
            return httpx.Response(
                200, json=self.probes.get(request.url.params.get("run_id", ""), [])
            )
        if method == "GET" and len(parts) == 5 and parts[3] == "probes":
            report = self.reports.get(parts[4])
            if report is None:
                return httpx.Response(404, json={"detail": f"Probe {parts[4]} not found"})
            return httpx.Response(200, json=report)
        if method == "GET" and path == "/api/openapi.json":
            return self._scripted("GET /api/openapi.json") or httpx.Response(
                200, json=self.openapi_doc
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    def _samples(self, dataset_id: str, params: Any) -> httpx.Response:
        """miStudio's ``get_dataset_samples`` (``datasets.py:791`` at c829a2cc): 1-indexed pages,
        ``limit`` at most 100, the pagination block as it builds it."""
        rows = self.samples.get(dataset_id)
        if rows is None:
            return httpx.Response(404, json={"detail": f"Dataset {dataset_id} not found"})
        page = int(params.get("page", 1))
        limit = int(params.get("limit", 20))
        if page < 1 or not 1 <= limit <= 100:
            return httpx.Response(422, json={"detail": [{"type": "less_than_equal"}]})
        start = (page - 1) * limit
        end = min(start + limit, len(rows))
        total_pages = (len(rows) + limit - 1) // limit
        return httpx.Response(
            200,
            json={
                "data": [{"index": i, "data": rows[i]} for i in range(start, end)],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": len(rows),
                    "total_pages": total_pages,
                    "has_next": page < total_pages,
                    "has_prev": page > 1,
                },
            },
        )

    def _download(self, body: dict[str, Any]) -> httpx.Response:
        extra = set(body) - DOWNLOAD_FIELDS
        if extra:
            return httpx.Response(
                422,
                json={"detail": [{"type": "extra_forbidden", "loc": ["body", sorted(extra)[0]]}]},
            )
        repo, split = body["repo_id"], body.get("split")
        for d in self.datasets.values():
            if (
                d["hf_repo_id"] == repo
                and d["metadata"]["split"] == split
                and d["metadata"]["config"] == body.get("config")
            ):
                return httpx.Response(
                    409, json={"detail": f"Dataset {repo} (split {split}) already exists"}
                )
        dataset_id = str(uuid.uuid4())
        self.datasets[dataset_id] = self._dataset(dataset_id, repo, split, "downloading")
        return httpx.Response(202, json=self.datasets[dataset_id])

    def _get_dataset(self, dataset_id: str) -> httpx.Response:
        d = self.datasets.get(dataset_id)
        if d is None:
            return httpx.Response(404, json={"detail": f"Dataset {dataset_id} not found"})
        self._polls[dataset_id] += 1
        key = (d["hf_repo_id"], d["metadata"]["split"])
        if d["status"] == "downloading" and self._polls[dataset_id] >= self.polls_until_ready:
            if key in self.download_errors:
                d["status"] = "error"
                d["error_message"] = self.download_errors[key]
            else:
                d["status"] = "ready"
        return httpx.Response(200, json=d)

    def _register(self, body: dict[str, Any]) -> httpx.Response:
        extra = set(body) - REGISTRATION_FIELDS
        if extra:
            return httpx.Response(
                422,
                json={
                    "detail": [
                        {
                            "type": "extra_forbidden",
                            "loc": ["body", sorted(extra)[0]],
                            "msg": "Extra inputs are not permitted",
                        }
                    ]
                },
            )
        d = self.datasets.get(str(body["dataset_id"]))
        if d is None:
            return httpx.Response(404, json={"detail": f"Dataset {body['dataset_id']} not found"})
        key = (d["hf_repo_id"], body["split"])
        counts = {"positive": 0, "negative": 0, "excluded": 0}
        unparseable = 0
        mapping = body["label_mapping"]
        for value, n in self.values.get(key, {}).items():
            # miStudio's map_label: a null label (seeded as "None") reads "None", then "null";
            # an unmapped value is counted unparseable, never dropped
            if value == "None":
                target = mapping.get("None") or mapping.get("null")
            else:
                target = mapping.get(value)
            if target in counts:
                counts[target] += n
            else:
                unparseable += n
        counts = dict(self.wrong_counts.get(key, counts))
        if body.get("role") != "calibration" and (
            counts["positive"] == 0 or counts["negative"] == 0
        ):
            return httpx.Response(
                422,
                json={
                    "detail": f"the view has {counts['positive']} positive and {counts['negative']} negative rows"
                },
            )
        view_id = f"pmd_{uuid.uuid4().hex[:12]}"
        view = {
            "id": view_id,
            **{
                k: body.get(k)
                for k in (
                    "name",
                    "dataset_id",
                    "config",
                    "split",
                    "input_column",
                    "label_column",
                    "label_mapping",
                    "pair_column",
                    "role",
                    "distribution",
                )
            },
            "keyword_filter": None,
            "counts": {
                "kinds": {"plain": sum(counts.values())},
                **counts,
                "unparseable": unparseable,
                "filtered_out": 0,
            },
            "created_at": "2026-10-07T00:00:00Z",
        }
        self.views[view_id] = view
        return httpx.Response(201, json=view)
