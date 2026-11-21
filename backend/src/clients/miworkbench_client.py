"""The ONLY module that calls miWorkbench (FTDD 009 section 5.4; ADR-001: HTTP only).

Behaviour copied from miWorkbench's own client rules (BRD-MIS-DATAWORKS-001 BR-001):

- a 2xx body that is not JSON raises :class:`MiWorkbenchNotJson` — never read as success (a misrouted
  ingress answers ``200`` with an HTML page, which miWorkbench once read as "nothing is steering");
- a connection error raises :class:`MiWorkbenchUnreachable`; a timeout :class:`MiWorkbenchTimeout`;
- ``409`` on a download raises :class:`MiWorkbenchDatasetExists` with miWorkbench's detail;
- ``409`` with ``detail.code == "positions_not_recorded"`` on an evaluation's scores raises
  :class:`MiWorkbenchPositionsNotRecorded` (miWorkbench 035 D29; feature 011);
- any other non-2xx raises :class:`MiWorkbenchRefused` with the status and miWorkbench's detail, VERBATIM.

**No credential reaches miWorkbench.** :meth:`MiWorkbenchClient.download` has no token parameter and builds
its body from an explicit key list; no method sets an ``Authorization`` header (FR-009.22; R-03.44).
miWorkbench reads a private repository with its own stored token.

Every call has a 30 s timeout (``MIWORKBENCH_TIMEOUT_SECONDS``). ``TRANSPORT`` is the test seam
(``tests/support/fake_miworkbench.py``), never set in production.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.config import get_settings
from ..core.env_names import setting_name

logger = logging.getLogger(__name__)

#: Test seam: an ``httpx`` transport (the fake miWorkbench). Production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None

#: Body keys of ``POST /api/v1/datasets/download``. ``revision`` only when XR-3 is served.
DOWNLOAD_KEYS: tuple[str, ...] = ("repo_id", "config", "split")
#: Page size of the per-row scores read (miWorkbench 035 D27: default 10,000, max 50,000).
SCORES_PAGE = 10_000
#: How a pinned dataset's commit is said when miWorkbench holds none for a row (miWorkbench 035 D1).
NOT_RECORDED = "revision not recorded"


def download_body(
    repo_id: str, config: str | None, split: str, revision: str | None = None
) -> dict[str, Any]:
    """The one body of ``POST /api/v1/datasets/download``: what the client sends and what a send
    step records (011 D49). ``revision`` only when given: miWorkbench forbids unknown fields, so an
    older miWorkbench must never see the key. There is no token key (FR-009.22)."""
    body: dict[str, Any] = {"repo_id": repo_id, "config": config, "split": split}
    if revision is not None:
        body["revision"] = revision
    return body


@dataclass(frozen=True)
class DatasetMatch:
    """A revision-aware lookup: the row at the commit, and the commits of the same view's other
    rows (``NOT_RECORDED`` for a row downloaded before miWorkbench pinned downloads)."""

    match: dict[str, Any] | None
    others: tuple[str, ...]


class MiWorkbenchError(Exception):
    """A miWorkbench call did not give a usable answer."""

    code = "mistudio_error"

    def __init__(self, message: str, *, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


class MiWorkbenchUnreachable(MiWorkbenchError):
    code = "mistudio_unreachable"


class MiWorkbenchTimeout(MiWorkbenchError):
    code = "mistudio_unreachable"


class MiWorkbenchNotJson(MiWorkbenchError):
    code = "mistudio_not_json"


class MiWorkbenchDatasetExists(MiWorkbenchError):
    code = "mistudio_dataset_exists"


class MiWorkbenchRefused(MiWorkbenchError):
    code = "mistudio_refused"


class MiWorkbenchPositionsNotRecorded(MiWorkbenchError):
    """miWorkbench holds no saved row positions for an evaluation (recorded before 035)."""

    code = "positions_not_recorded"


class MiWorkbenchScoresRefused(MiWorkbenchError):
    """A scores page that does not belong to the requested evaluation, or pages that do not add
    up to the total miWorkbench stated: the rows are not used."""

    code = "mistudio_scores_refused"


def _refusal_code(response: httpx.Response) -> str | None:
    """``detail.code`` of a miWorkbench refusal (``{"detail": {"code", "message"}}``), or None."""
    try:
        body = response.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return str(code) if code is not None else None


def _json_or_raise(response: httpx.Response, what: str) -> Any:
    """The JSON body of a 2xx response, or the matching error. A 2xx non-JSON body raises."""
    if response.status_code // 100 != 2:
        try:
            body = response.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except ValueError:
            detail = response.text[:2000]
        raise MiWorkbenchRefused(
            f"miWorkbench refused {what}: HTTP {response.status_code}.",
            status=response.status_code,
            detail=detail,
        )
    try:
        return response.json()
    except ValueError:
        content_type = response.headers.get("content-type", "")
        raise MiWorkbenchNotJson(
            f"miWorkbench answered {what} with HTTP {response.status_code} and a body that is not "
            f"JSON ({content_type or 'no content type'}). Check that the miWorkbench URL points at "
            "miWorkbench's backend.",
            status=response.status_code,
        ) from None


class MiWorkbenchClient:
    """Synchronous client (the send worker; routes call it from a thread)."""

    def __init__(self, base_url: str, *, timeout: float | None = None) -> None:
        if not base_url:
            raise MiWorkbenchUnreachable(
                f"No miWorkbench URL is configured ({setting_name('mistudio_base_url')})."
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout if timeout is not None else get_settings().mistudio_timeout_seconds
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=TRANSPORT,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MiWorkbenchClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _call(
        self,
        method: str,
        path: str,
        what: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return self._client.request(method, path, json=json, params=params)
        except httpx.TimeoutException as exc:
            raise MiWorkbenchTimeout(
                f"miWorkbench did not answer {what} within {self.timeout:.0f} s at {self.base_url}."
            ) from exc
        except httpx.HTTPError as exc:
            raise MiWorkbenchUnreachable(
                f"miWorkbench did not answer at {self.base_url} ({type(exc).__name__}); check the "
                "Other apps setting, then resume."
            ) from exc

    # --- datasets ------------------------------------------------------------------------------

    def download(
        self, repo_id: str, config: str | None, split: str, *, revision: str | None = None
    ) -> dict[str, Any]:
        """``POST /api/v1/datasets/download``. There is no token parameter (FR-009.22)."""
        body = download_body(repo_id, config, split, revision)
        response = self._call("POST", "/api/v1/datasets/download", "the download", json=body)
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:2000]
            raise MiWorkbenchDatasetExists(
                f"miWorkbench already holds {repo_id} split {split}.", status=409, detail=detail
            )
        data: dict[str, Any] = _json_or_raise(response, "the download")
        return data

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/datasets/{dataset_id}", "a dataset read")
        data: dict[str, Any] = _json_or_raise(response, "a dataset read")
        return data

    def _same_view(self, repo_id: str, config: str | None, split: str) -> list[dict[str, Any]]:
        response = self._call(
            "GET",
            "/api/v1/datasets",
            "the dataset list",
            params={"search": repo_id, "limit": 100},
        )
        data = _json_or_raise(response, "the dataset list")
        rows = data["data"] if isinstance(data, dict) else data
        out = []
        for row in rows:
            meta = row.get("metadata") or {}
            if (
                row.get("hf_repo_id") == repo_id
                and meta.get("split") == split
                and meta.get("config") == config
            ):
                out.append(dict(row))
        return out

    def find_dataset(self, repo_id: str, config: str | None, split: str) -> dict[str, Any] | None:
        """The dataset miWorkbench holds for (repo, config, split): reported on a ``409`` only."""
        rows = self._same_view(repo_id, config, split)
        return rows[0] if rows else None

    def find_dataset_at(
        self, repo_id: str, config: str | None, split: str, revision: str
    ) -> DatasetMatch:
        """The dataset miWorkbench holds for (repo, config, split) AT ``revision`` (miWorkbench 035: the
        commit is part of the download identity, D1/D8). A row of the same view at another commit,
        or with no recorded commit, is never the match; its commit is reported in ``others``."""
        match = None
        others: list[str] = []
        for row in self._same_view(repo_id, config, split):
            recorded = row.get("revision")
            if recorded == revision and match is None:
                match = row
            else:
                others.append(str(recorded) if recorded else NOT_RECORDED)
        return DatasetMatch(match, tuple(others))

    # --- probe monitors ------------------------------------------------------------------------

    def register_view(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/v1/probe-monitors/datasets`` with a body from ``plan.registration_body``."""
        response = self._call(
            "POST", "/api/v1/probe-monitors/datasets", "the registration", json=dict(body)
        )
        data: dict[str, Any] = _json_or_raise(response, "the registration")
        return data

    def get_view(self, view_id: str) -> dict[str, Any] | None:
        """``GET /api/v1/probe-monitors/datasets/{id}``; None when miWorkbench no longer has it."""
        response = self._call("GET", f"/api/v1/probe-monitors/datasets/{view_id}", "the view")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = _json_or_raise(response, "the view")
        return data

    def update_view_columns(self, view_id: str, columns: Sequence[str]) -> dict[str, Any]:
        """``PATCH .../datasets/{id}/columns``: additive, so ``columns`` holds every column the view
        already declares plus the new ones (miWorkbench refuses a removal, ``column_not_additive``).
        """
        response = self._call(
            "PATCH",
            f"/api/v1/probe-monitors/datasets/{view_id}/columns",
            "the view's columns",
            json={"group_columns": list(columns)},
        )
        data: dict[str, Any] = _json_or_raise(response, "the view's columns")
        return data

    def list_views(self) -> list[dict[str, Any]]:
        """Every probe-dataset view, ARCHIVED ones included: miWorkbench's list hides an archived
        view unless ``include_archived=true`` (coordinator, 2026-10-21), and an archived view with
        evaluations is still the evaluation a reproduction link names. A miWorkbench from before
        that archive ignores the parameter (FastAPI drops unknown query parameters; its route
        takes ``role`` only), so the list is complete either way."""
        response = self._call(
            "GET",
            "/api/v1/probe-monitors/datasets",
            "the view list",
            params={"include_archived": "true"},
        )
        data: list[dict[str, Any]] = _json_or_raise(response, "the view list")
        return data

    def list_runs(
        self, limit: int = 200, *, train_dataset_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Newest runs first. ``train_dataset_id`` only when miWorkbench serves the filter (035 XR-4,
        capability ``runs_train_filter``); without it miWorkbench has no filter (T-50)."""
        params: dict[str, Any] = {"limit": limit}
        if train_dataset_id is not None:
            params["train_dataset_id"] = train_dataset_id
        response = self._call("GET", "/api/v1/probe-monitors/runs", "the run list", params=params)
        data: list[dict[str, Any]] = _json_or_raise(response, "the run list")
        return data[:limit]

    def list_probes(self, run_id: str) -> list[dict[str, Any]]:
        response = self._call(
            "GET", "/api/v1/probe-monitors/probes", "the probe list", params={"run_id": run_id}
        )
        data: list[dict[str, Any]] = _json_or_raise(response, "the probe list")
        return data

    def get_report(self, probe_id: str) -> dict[str, Any] | None:
        """A probe's report, or None when miWorkbench no longer has the probe (``404``)."""
        response = self._call("GET", f"/api/v1/probe-monitors/probes/{probe_id}", "a probe report")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = _json_or_raise(response, "a probe report")
        return data

    def get_behaviour(self, key: str) -> dict[str, Any] | None:
        """``GET /api/v1/probe-monitors/behaviours/{key}`` (miWorkbench 037's catalogue; 017
        FR-017.10): the head (``key``, ``current_version``, ``archived``) and its current
        ``definition``. None when miWorkbench has no catalogued behaviour of that key: its catalogue's
        ``404`` with ``detail.code == "not_found"``. Any other ``404`` (a miWorkbench without 037's
        catalogue, or a proxy answering for it) is a refusal, never "not in the catalogue"."""
        response = self._call(
            "GET", f"/api/v1/probe-monitors/behaviours/{key}", "a behaviour catalogue read"
        )
        if response.status_code == 404 and _refusal_code(response) == "not_found":
            return None
        data: dict[str, Any] = _json_or_raise(response, "a behaviour catalogue read")
        return data

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """``GET /api/v1/probe-monitors/runs/{run_id}`` (its ``environment`` says how the run
        rendered rows), or None when miWorkbench no longer has the run (``404``)."""
        response = self._call("GET", f"/api/v1/probe-monitors/runs/{run_id}", "a run read")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = _json_or_raise(response, "a run read")
        return data

    def dataset_samples(self, dataset_id: str, page: int, limit: int = 100) -> dict[str, Any]:
        """``GET /api/v1/datasets/{dataset_id}/samples``: one page of the dataset's rows as miWorkbench
        stored them, ``{data: [{index, data}], pagination: {total, has_next, ...}}``.

        miWorkbench serves a multi-split dataset's ``train`` split (else its first) and does not say
        which; a caller ties the served rows to a view by its recorded counts, never by trust."""
        response = self._call(
            "GET",
            f"/api/v1/datasets/{dataset_id}/samples",
            "a dataset samples read",
            params={"page": page, "limit": limit},
        )
        data: dict[str, Any] = _json_or_raise(response, "a dataset samples read")
        return data

    def evaluation_scores(
        self, probe_id: str, evaluation_id: str, offset: int = 0, limit: int | None = None
    ) -> dict[str, Any]:
        """One page of an evaluation's per-row scores (miWorkbench 035 XR-2):
        ``{evaluation_id, probe_id, dataset_id, source_dataset_id, revision, status,
        reported_auroc, total, offset, next_offset, rows: [{position, score, label, n_scored}]}``.
        A ``409 positions_not_recorded`` raises :class:`MiWorkbenchPositionsNotRecorded` with
        miWorkbench's detail; other refusals raise :class:`MiWorkbenchRefused` verbatim."""
        what = "an evaluation's scores"
        response = self._call(
            "GET",
            f"/api/v1/probe-monitors/probes/{probe_id}/evaluations/{evaluation_id}/scores",
            what,
            params={"offset": offset, "limit": SCORES_PAGE if limit is None else limit},
        )
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict) and detail.get("code") == "positions_not_recorded":
                raise MiWorkbenchPositionsNotRecorded(
                    f"miWorkbench refused {what}: {detail.get('message') or 'positions not recorded'}"
                    f" ({detail.get('evaluate') or 're-evaluate the probe'}).",
                    status=409,
                    detail=detail,
                )
        data: dict[str, Any] = _json_or_raise(response, what)
        return data

    def iter_evaluation_scores(self, probe_id: str, evaluation_id: str) -> dict[str, Any]:
        """Every page of one evaluation's scores, concatenated: the first page's header fields
        with ``rows`` holding all rows. Refuses a page of another evaluation and a row count that
        differs from the ``total`` miWorkbench stated."""
        first: dict[str, Any] | None = None
        rows: list[dict[str, Any]] = []
        offset: int | None = 0
        for page in self._pages(probe_id, evaluation_id):
            if str(page["evaluation_id"]) != evaluation_id:
                raise MiWorkbenchScoresRefused(
                    f"miWorkbench answered the scores of evaluation {evaluation_id} with evaluation "
                    f"{page['evaluation_id']}; the rows are not used."
                )
            first = first or page
            rows.extend(page["rows"])
            offset = page["next_offset"]
        assert first is not None and offset is None
        if len(rows) != int(first["total"]):
            raise MiWorkbenchScoresRefused(
                f"miWorkbench stated {first['total']} scored rows for evaluation {evaluation_id} and "
                f"served {len(rows)}; the rows are not used."
            )
        return {**first, "offset": 0, "next_offset": None, "rows": rows}

    def _pages(self, probe_id: str, evaluation_id: str) -> Iterator[dict[str, Any]]:
        offset: int | None = 0
        while offset is not None:
            page = self.evaluation_scores(probe_id, evaluation_id, offset)
            yield page
            next_offset = page["next_offset"]
            if next_offset is not None and int(next_offset) <= offset:
                raise MiWorkbenchScoresRefused(
                    f"miWorkbench's scores pages for evaluation {evaluation_id} do not advance "
                    f"(offset {offset}, next {next_offset})."
                )
            offset = None if next_offset is None else int(next_offset)

    # --- capabilities --------------------------------------------------------------------------

    def openapi(self) -> dict[str, Any]:
        """miWorkbench's served schema at ``/api/openapi.json`` (``main.py:76``; Stage 3)."""
        response = self._call("GET", "/api/openapi.json", "the OpenAPI read")
        data: dict[str, Any] = _json_or_raise(response, "the OpenAPI read")
        return data
