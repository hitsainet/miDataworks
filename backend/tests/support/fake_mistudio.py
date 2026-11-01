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

**miStudio 035 (feature 011, D52).** Three toggles, each changing BOTH the served OpenAPI document
and the behaviour, as the real deploy would:

- ``serves_revision`` (XR-3): ``DatasetDownloadRequest`` and ``DatasetResponse`` gain ``revision``;
  a download's identity is (repo, config, split, revision) and the row records the commit; without
  it a ``revision`` body key is refused ``422`` (``extra="forbid"``) and rows carry no ``revision``;
- ``serves_run_filter`` (XR-4): ``GET /probe-monitors/runs`` lists a ``train_dataset_id`` query
  parameter and filters by it; without it the parameter is ignored, as FastAPI ignores an unknown
  query parameter. Runs are served newest first and cut at ``limit``, as miStudio does;
- ``serves_scores`` (XR-2): ``GET /probe-monitors/probes/{p}/evaluations/{e}/scores`` pages
  ``scores[(p, e)]`` by ``offset``/``limit`` (``limit`` 1..50,000) in the 035 FTDD section 5.4
  shape; an evaluation in ``legacy_evaluations`` answers ``409 positions_not_recorded``.

**miStudio 034 FR-26 (XR-1; 009 FTASKS 17.1).** ``serves_manifest`` adds
``dataset_version_manifest`` to ``ProbeDatasetCreate`` (without it the key is refused ``422``, as
``extra="forbid"`` refuses it) and checks it the way miStudio ``516a2ecb``+
``probe_monitors.py::create_probe_dataset`` does: the split must be named by the manifest
(``content.splits[].name``) else ``409``; the manifest's ``rows`` must equal the downloaded split's
row count BEFORE the label mapping, else ``409`` with miStudio's own detail string. A stale or
different split is scripted with ``downloaded_rows``; a passing check returns ``dataset_version``.

The shapes are miStudio's staged 035 FTDD sections 5.1 to 5.4
(``~/app/miSuite/specs/ENH-002-dataworks-provenance-routes/mistudio/0xcc/tdds/``).
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

#: The four names miStudio 035 owns (BRD-MIS-DATAWORKS-002 section 6), as this fake serves them.
REVISION_PROPERTY = "revision"
SCORES_PATH = "/api/v1/probe-monitors/probes/{probe_id}/evaluations/{evaluation_id}/scores"
RUN_FILTER_PARAM = "train_dataset_id"
SCORES_MAX_LIMIT = 50_000
MANIFEST_PROPERTY = "dataset_version_manifest"
#: miStudio 037 (ENH-005 D37): XR-5's two ``ProbeDatasetCreate`` properties.
BEHAVIOUR_PROPERTIES = ("behaviour", "positive_meaning")


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
    #: View ids miStudio has ARCHIVED (miStudio's probe-view archive, 2026-10-21): its list hides
    #: them unless ``include_archived=true``; an older miStudio ignores the parameter.
    archived: set[str] = field(default_factory=set)
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
    #: 017 (FR-017.30): (repo, split, label column) -> label value counts, for sets registering
    #: one split under several label columns (a sub-question set); falls back to ``values``.
    values_by_column: dict[tuple[str, str, str], dict[str, int]] = field(default_factory=dict)
    #: (repo, split) -> counts override (a stale or wrong split).
    wrong_counts: dict[tuple[str, str], dict[str, int]] = field(default_factory=dict)
    #: download ids whose status ends in error, with miStudio's message.
    download_errors: dict[tuple[str, str], str] = field(default_factory=dict)
    #: miStudio 035 toggles (see the module docstring).
    serves_revision: bool = False
    serves_run_filter: bool = False
    serves_scores: bool = False
    #: miStudio 034 FR-26 toggle (see the module docstring).
    serves_manifest: bool = False
    #: miStudio 037 toggle (015 XR-5): ``behaviour`` and ``positive_meaning`` on
    #: ``ProbeDatasetCreate`` (refused 422 without it, as ``extra="forbid"`` refuses them), kept on
    #: the view. ``serves_behaviour_only`` serves ONE of the two (a half-served pair).
    serves_behaviour: bool = False
    serves_behaviour_only: bool = False
    #: (repo, split) -> the row count of the split miStudio downloaded, when it is not the seeded
    #: values' total (a stale or different split; scripted, never accidental).
    downloaded_rows: dict[tuple[str, str], int] = field(default_factory=dict)
    #: (repo, split) -> the ``revision`` a read of that view's datasets reports instead of the one
    #: it was downloaded at (a scripted mismatch; ``None`` reports "not recorded").
    revision_override: dict[tuple[str, str], str | None] = field(default_factory=dict)
    #: (probe id, evaluation id) -> the evaluation's header and rows (035 FTDD section 5.4).
    scores: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: evaluations recorded before 035 saved positions: ``409 positions_not_recorded``.
    legacy_evaluations: set[str] = field(default_factory=set)
    #: evaluation id -> the ``evaluation_id`` its pages claim (a scripted wrong answer).
    evaluation_id_override: dict[str, str] = field(default_factory=dict)
    #: miStudio 037's behaviour catalogue (017 FR-017.10): key -> the head as
    #: ``GET /probe-monitors/behaviours/{key}`` serves it (``catalogue.head_out``); see
    #: :func:`catalogue_head`. A key not here answers miStudio's ``404 not_found``.
    behaviours: dict[str, dict[str, Any]] = field(default_factory=dict)
    _polls: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        if not self.openapi_doc:
            self.openapi_doc = json.loads((FIXTURES / "mistudio_openapi.json").read_text())

    def document(self) -> dict[str, Any]:
        """The OpenAPI document served now: the recorded fixture plus each served 035 name."""
        doc = json.loads(json.dumps(self.openapi_doc))
        schemas = doc.setdefault("components", {}).setdefault("schemas", {})
        paths = doc.setdefault("paths", {})
        if self.serves_manifest:
            schema = schemas.setdefault("ProbeDatasetCreate", {"type": "object", "properties": {}})
            schema.setdefault("properties", {})[MANIFEST_PROPERTY] = {
                "anyOf": [{"type": "object"}, {"type": "null"}]
            }
        if self.serves_behaviour or self.serves_behaviour_only:
            schema = schemas.setdefault("ProbeDatasetCreate", {"type": "object", "properties": {}})
            served = BEHAVIOUR_PROPERTIES if self.serves_behaviour else BEHAVIOUR_PROPERTIES[:1]
            for prop in served:
                schema.setdefault("properties", {})[prop] = {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                }
        if self.serves_revision:
            for name in ("DatasetDownloadRequest", "DatasetResponse"):
                schema = schemas.setdefault(name, {"type": "object", "properties": {}})
                schema.setdefault("properties", {})[REVISION_PROPERTY] = {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                }
        if self.serves_run_filter:
            get = paths.setdefault("/api/v1/probe-monitors/runs", {}).setdefault("get", {})
            get["parameters"] = [
                {"name": "status", "in": "query", "required": False},
                {"name": "limit", "in": "query", "required": False},
                {"name": RUN_FILTER_PARAM, "in": "query", "required": False},
            ]
        if self.serves_scores:
            paths[SCORES_PATH] = {
                "get": {
                    "parameters": [
                        {"name": "probe_id", "in": "path", "required": True},
                        {"name": "evaluation_id", "in": "path", "required": True},
                        {"name": "offset", "in": "query", "required": False},
                        {"name": "limit", "in": "query", "required": False},
                    ]
                }
            }
        return doc

    # --- helpers for tests ---------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str, prefix: str) -> list[Captured]:
        return [r for r in self.requests if r.method == method and r.path.startswith(prefix)]

    def count(self, method: str, prefix: str) -> int:
        return len(self.calls(method, prefix))

    def fail(self, key: str, **response: Any) -> None:
        self.script.setdefault(key, []).append(response)

    def seed_existing_dataset(
        self, repo_id: str, split: str, *, revision: str | None = None
    ) -> str:
        """A ready dataset miStudio already holds; ``revision`` None is a pre-035 (legacy) row."""
        dataset_id = str(uuid.uuid4())
        self.datasets[dataset_id] = self._dataset(dataset_id, repo_id, split, "ready", revision)
        return dataset_id

    # --- the transport -------------------------------------------------------------------------

    def _dataset(
        self,
        dataset_id: str,
        repo_id: str,
        split: str,
        status: str,
        revision: str | None = None,
    ) -> dict[str, Any]:
        return {
            "_revision": revision,
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
                200,
                json={"data": [self._served(d) for d in self.datasets.values()], "pagination": {}},
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
            shown = request.url.params.get("include_archived") == "true"
            return httpx.Response(
                200,
                json=[v for k, v in self.views.items() if shown or k not in self.archived],
            )
        if method == "GET" and path == "/api/v1/probe-monitors/runs":
            return self._scripted("GET /probe-monitors/runs") or self._runs(request.url.params)
        if (
            method == "GET"
            and len(parts) == 8
            and parts[:4] == ["api", "v1", "probe-monitors", "probes"]
            and parts[5] == "evaluations"
            and parts[7] == "scores"
            and self.serves_scores
        ):
            return self._scripted("GET /probe-monitors/probes/{p}/evaluations/{e}/scores") or (
                self._scores(parts[4], parts[6], request.url.params)
            )
        if method == "GET" and path == "/api/v1/probe-monitors/probes":
            return httpx.Response(
                200, json=self.probes.get(request.url.params.get("run_id", ""), [])
            )
        if (
            method == "GET"
            and len(parts) == 5
            and parts[:4] == ["api", "v1", "probe-monitors", "behaviours"]
        ):
            return self._scripted("GET /probe-monitors/behaviours/{key}") or self._behaviour(
                parts[4]
            )
        if method == "GET" and len(parts) == 5 and parts[3] == "probes":
            report = self.reports.get(parts[4])
            if report is None:
                return httpx.Response(404, json={"detail": f"Probe {parts[4]} not found"})
            return httpx.Response(200, json=report)
        if method == "GET" and path == "/api/openapi.json":
            return self._scripted("GET /api/openapi.json") or httpx.Response(
                200, json=self.document()
            )
        return httpx.Response(404, json={"detail": "Not Found"})

    def _behaviour(self, key: str) -> httpx.Response:
        found = self.behaviours.get(key)
        if found is None:
            # miStudio behaviour_catalogue.get_head: BehaviourRefused(404, "not_found", ...)
            # raised as HTTPException(detail=refusal.as_detail()).
            return httpx.Response(
                404,
                json={
                    "detail": {
                        "code": "not_found",
                        "message": f"no behaviour {key!r} is catalogued; create it to "
                        "catalogue the key",
                        "next": None,
                    }
                },
            )
        return httpx.Response(200, json=found)

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
        allowed = DOWNLOAD_FIELDS | ({REVISION_PROPERTY} if self.serves_revision else set())
        extra = set(body) - allowed
        if extra:
            return httpx.Response(
                422,
                json={"detail": [{"type": "extra_forbidden", "loc": ["body", sorted(extra)[0]]}]},
            )
        repo, split = body["repo_id"], body.get("split")
        # 035 D8: any ref resolves to a commit; this fake's "Hub" resolves a ref to itself.
        revision = body.get(REVISION_PROPERTY) or None if self.serves_revision else None
        for d in self.datasets.values():
            if (
                d["hf_repo_id"] == repo
                and d["metadata"]["split"] == split
                and d["metadata"]["config"] == body.get("config")
                # 035 D1/D10: the commit is part of the identity; a legacy NULL row never matches
                # a pinned request.
                and (not self.serves_revision or d["_revision"] == revision)
            ):
                at = f" at {revision}" if self.serves_revision else ""
                return httpx.Response(
                    409,
                    json={
                        "detail": f"Dataset {repo} (split {split}){at} already exists "
                        f"(dataset {d['id']})"
                    },
                )
        dataset_id = str(uuid.uuid4())
        self.datasets[dataset_id] = self._dataset(dataset_id, repo, split, "downloading", revision)
        return httpx.Response(202, json=self._served(self.datasets[dataset_id]))

    def _served(self, d: dict[str, Any]) -> dict[str, Any]:
        """A dataset row as miStudio serves it: ``revision`` only when XR-3 is served."""
        out = {k: v for k, v in d.items() if not k.startswith("_")}
        if self.serves_revision:
            view = (d["hf_repo_id"], d["metadata"]["split"])
            out[REVISION_PROPERTY] = self.revision_override.get(view, d["_revision"])
        return out

    def _runs(self, params: Any) -> httpx.Response:
        """miStudio's run list: newest first (the order ``runs`` holds), the filter when served
        (an unserved query parameter is ignored), cut at ``limit`` (at most 200)."""
        limit = int(params.get("limit", 50))
        if not 1 <= limit <= 200:
            return httpx.Response(422, json={"detail": [{"type": "less_than_equal"}]})
        runs = self.runs
        wanted = params.get(RUN_FILTER_PARAM)
        if self.serves_run_filter and wanted:
            runs = [r for r in runs if r.get("train_dataset_id") == wanted]
        return httpx.Response(200, json=runs[:limit])

    def _scores(self, probe_id: str, evaluation_id: str, params: Any) -> httpx.Response:
        """035 FTDD section 5.4, in its order of checks."""
        served = self.scores.get((probe_id, evaluation_id))
        if served is None and evaluation_id not in self.legacy_evaluations:
            return httpx.Response(
                404, json={"detail": f"Evaluation {evaluation_id} of probe {probe_id} not found"}
            )
        if evaluation_id in self.legacy_evaluations:
            evaluate = f"POST /api/v1/probe-monitors/probes/{probe_id}/evaluate"
            return httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "positions_not_recorded",
                        "message": f"Evaluation {evaluation_id} was recorded before miStudio "
                        "saved row positions; re-evaluate the probe to serve its scores.",
                        "evaluate": evaluate,
                    }
                },
            )
        assert served is not None
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 10_000))
        if offset < 0 or not 1 <= limit <= SCORES_MAX_LIMIT:
            return httpx.Response(422, json={"detail": [{"type": "less_than_equal"}]})
        rows = served["rows"]
        total = len(rows)
        end = offset + limit
        return httpx.Response(
            200,
            json={
                "evaluation_id": self.evaluation_id_override.get(evaluation_id, evaluation_id),
                "probe_id": probe_id,
                "dataset_id": served["dataset_id"],
                "source_dataset_id": served["source_dataset_id"],
                "revision": served["revision"],
                "status": served.get("status", "completed"),
                "reported_auroc": served["reported_auroc"],
                "total": total,
                "offset": offset,
                "next_offset": end if end < total else None,
                "rows": rows[offset:end],
            },
        )

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
        return httpx.Response(200, json=self._served(d))

    def _register(self, body: dict[str, Any]) -> httpx.Response:
        allowed = (
            REGISTRATION_FIELDS
            | ({MANIFEST_PROPERTY} if self.serves_manifest else set())
            | (set(BEHAVIOUR_PROPERTIES) if self.serves_behaviour else set())
        )
        extra = set(body) - allowed
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
        seeded = self.values_by_column.get((*key, str(body.get("label_column"))))
        for value, n in (seeded if seeded is not None else self.values.get(key, {})).items():
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
        manifest = body.get(MANIFEST_PROPERTY)
        verified = None
        if manifest is not None:
            split = body["split"]
            splits = [
                s
                for s in ((manifest.get("content") or {}).get("splits") or [])
                if isinstance(s, dict)
            ]
            expected_rows = next((s.get("rows") for s in splits if s.get("name") == split), None)
            if expected_rows is None:
                names = [s["name"] for s in splits if "name" in s]
                return httpx.Response(
                    409,
                    json={
                        "detail": f"split {split!r} is not described by the manifest; it describes {names}"
                    },
                )
            total = self.downloaded_rows.get(key, sum(self.values.get(key, {}).values()))
            if total != expected_rows:
                return httpx.Response(
                    409,
                    json={
                        "detail": (
                            f"split {split!r}: manifest states {expected_rows} rows, "
                            f"downloaded split has {total}"
                        )
                    },
                )
            publication = manifest.get("publication") or {}
            verified = {
                "kind": "mistudio.verified-dataset-version/1",
                "version_id": (manifest.get("version") or {}).get("version_id"),
                "split": split,
                "manifest_rows": expected_rows,
                "downloaded_rows": total,
                "repository": publication.get("repo_id"),
                "commit": publication.get("commit"),
                "publication_state": publication.get("state"),
            }
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
            "dataset_version": verified,
        }
        if self.serves_behaviour:
            view.update({p: body.get(p) for p in BEHAVIOUR_PROPERTIES})
        self.views[view_id] = view
        return httpx.Response(201, json=view)


def catalogue_head(
    key: str,
    *,
    version: int = 1,
    positive_meaning: str = "the text shows the behaviour",
    negative_meaning: str | None = "the text does not",
    target_fpr: float | None = 0.01,
    archived: bool = False,
) -> dict[str, Any]:
    """A catalogue head in miStudio 037's ``head_out`` shape (``behaviour_catalogue.py``, read at
    miStudio ``98e506b7``), with the definition ``version_out`` writes. Fields miDataworks does not
    read are present, as miStudio serves them, so a reader keyed on the wrong name fails."""
    return {
        "key": key,
        "catalogued": True,
        "current_version": version,
        "archived": archived,
        "archived_at": "2026-10-20T00:00:00Z" if archived else None,
        "archived_by": "operator" if archived else None,
        "created_by": "operator",
        "created_at": "2026-10-20T00:00:00Z",
        "updated_at": "2026-10-21T00:00:00Z",
        "definition": {
            "version": version,
            "name": key,
            "description": None,
            "positive_meaning": positive_meaning,
            "negative_meaning": negative_meaning,
            "target_fpr": target_fpr,
            "created_by": "operator",
            "created_at": "2026-10-21T00:00:00Z",
        },
        "adopted": {},
        "counts": {},
    }
