"""The ONLY module that calls miStudio (FTDD 009 section 5.4; ADR-001: HTTP only).

Behaviour copied from miStudio's own client rules (BRD-MIS-DATAWORKS-001 BR-001):

- a 2xx body that is not JSON raises :class:`MiStudioNotJson` — never read as success (a misrouted
  ingress answers ``200`` with an HTML page, which miStudio once read as "nothing is steering");
- a connection error raises :class:`MiStudioUnreachable`; a timeout :class:`MiStudioTimeout`;
- ``409`` on a download raises :class:`MiStudioDatasetExists` with miStudio's detail;
- any other non-2xx raises :class:`MiStudioRefused` with the status and miStudio's detail, VERBATIM.

**No credential reaches miStudio.** :meth:`MiStudioClient.download` has no token parameter and builds
its body from an explicit key list; no method sets an ``Authorization`` header (FR-009.22; R-03.44).
miStudio reads a private repository with its own stored token.

Every call has a 30 s timeout (``MISTUDIO_TIMEOUT_SECONDS``). ``TRANSPORT`` is the test seam
(``tests/support/fake_mistudio.py``), never set in production.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from ..core.config import get_settings

logger = logging.getLogger(__name__)

#: Test seam: an ``httpx`` transport (the fake miStudio). Production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None

#: Body keys of ``POST /api/v1/datasets/download``. ``revision`` only when XR-3 is served.
DOWNLOAD_KEYS: tuple[str, ...] = ("repo_id", "config", "split")


class MiStudioError(Exception):
    """A miStudio call did not give a usable answer."""

    code = "mistudio_error"

    def __init__(self, message: str, *, status: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


class MiStudioUnreachable(MiStudioError):
    code = "mistudio_unreachable"


class MiStudioTimeout(MiStudioError):
    code = "mistudio_unreachable"


class MiStudioNotJson(MiStudioError):
    code = "mistudio_not_json"


class MiStudioDatasetExists(MiStudioError):
    code = "mistudio_dataset_exists"


class MiStudioRefused(MiStudioError):
    code = "mistudio_refused"


def _json_or_raise(response: httpx.Response, what: str) -> Any:
    """The JSON body of a 2xx response, or the matching error. A 2xx non-JSON body raises."""
    if response.status_code // 100 != 2:
        try:
            body = response.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
        except ValueError:
            detail = response.text[:2000]
        raise MiStudioRefused(
            f"miStudio refused {what}: HTTP {response.status_code}.",
            status=response.status_code,
            detail=detail,
        )
    try:
        return response.json()
    except ValueError:
        content_type = response.headers.get("content-type", "")
        raise MiStudioNotJson(
            f"miStudio answered {what} with HTTP {response.status_code} and a body that is not "
            f"JSON ({content_type or 'no content type'}). Check that the miStudio URL points at "
            "miStudio's backend.",
            status=response.status_code,
        ) from None


class MiStudioClient:
    """Synchronous client (the send worker; routes call it from a thread)."""

    def __init__(self, base_url: str, *, timeout: float | None = None) -> None:
        if not base_url:
            raise MiStudioUnreachable("No miStudio URL is configured (MISTUDIO_BASE_URL).")
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

    def __enter__(self) -> MiStudioClient:
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
            raise MiStudioTimeout(
                f"miStudio did not answer {what} within {self.timeout:.0f} s at {self.base_url}."
            ) from exc
        except httpx.HTTPError as exc:
            raise MiStudioUnreachable(
                f"miStudio did not answer at {self.base_url} ({type(exc).__name__}); check the "
                "Other apps setting, then resume."
            ) from exc

    # --- datasets ------------------------------------------------------------------------------

    def download(
        self, repo_id: str, config: str | None, split: str, *, revision: str | None = None
    ) -> dict[str, Any]:
        """``POST /api/v1/datasets/download``. There is no token parameter (FR-009.22)."""
        body: dict[str, Any] = {"repo_id": repo_id, "config": config, "split": split}
        if revision is not None:
            body["revision"] = revision
        response = self._call("POST", "/api/v1/datasets/download", "the download", json=body)
        if response.status_code == 409:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:2000]
            raise MiStudioDatasetExists(
                f"miStudio already holds {repo_id} split {split}.", status=409, detail=detail
            )
        data: dict[str, Any] = _json_or_raise(response, "the download")
        return data

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/datasets/{dataset_id}", "a dataset read")
        data: dict[str, Any] = _json_or_raise(response, "a dataset read")
        return data

    def find_dataset(self, repo_id: str, config: str | None, split: str) -> dict[str, Any] | None:
        """The dataset miStudio holds for (repo, config, split): reported on a ``409`` only."""
        response = self._call(
            "GET",
            "/api/v1/datasets",
            "the dataset list",
            params={"search": repo_id, "limit": 100},
        )
        data = _json_or_raise(response, "the dataset list")
        rows = data["data"] if isinstance(data, dict) else data
        for row in rows:
            meta = row.get("metadata") or {}
            if (
                row.get("hf_repo_id") == repo_id
                and meta.get("split") == split
                and meta.get("config") == config
            ):
                return dict(row)
        return None

    # --- probe monitors ------------------------------------------------------------------------

    def register_view(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/v1/probe-monitors/datasets`` with a body from ``plan.registration_body``."""
        response = self._call(
            "POST", "/api/v1/probe-monitors/datasets", "the registration", json=dict(body)
        )
        data: dict[str, Any] = _json_or_raise(response, "the registration")
        return data

    def list_views(self) -> list[dict[str, Any]]:
        response = self._call("GET", "/api/v1/probe-monitors/datasets", "the view list")
        data: list[dict[str, Any]] = _json_or_raise(response, "the view list")
        return data

    def list_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        """Newest runs first; miStudio has no filter by training view (T-50)."""
        response = self._call(
            "GET", "/api/v1/probe-monitors/runs", "the run list", params={"limit": limit}
        )
        data: list[dict[str, Any]] = _json_or_raise(response, "the run list")
        return data[:limit]

    def list_probes(self, run_id: str) -> list[dict[str, Any]]:
        response = self._call(
            "GET", "/api/v1/probe-monitors/probes", "the probe list", params={"run_id": run_id}
        )
        data: list[dict[str, Any]] = _json_or_raise(response, "the probe list")
        return data

    def get_report(self, probe_id: str) -> dict[str, Any] | None:
        """A probe's report, or None when miStudio no longer has the probe (``404``)."""
        response = self._call("GET", f"/api/v1/probe-monitors/probes/{probe_id}", "a probe report")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = _json_or_raise(response, "a probe report")
        return data

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """``GET /api/v1/probe-monitors/runs/{run_id}`` (its ``environment`` says how the run
        rendered rows), or None when miStudio no longer has the run (``404``)."""
        response = self._call("GET", f"/api/v1/probe-monitors/runs/{run_id}", "a run read")
        if response.status_code == 404:
            return None
        data: dict[str, Any] = _json_or_raise(response, "a run read")
        return data

    def dataset_samples(self, dataset_id: str, page: int, limit: int = 100) -> dict[str, Any]:
        """``GET /api/v1/datasets/{dataset_id}/samples``: one page of the dataset's rows as miStudio
        stored them, ``{data: [{index, data}], pagination: {total, has_next, ...}}``.

        miStudio serves a multi-split dataset's ``train`` split (else its first) and does not say
        which; a caller ties the served rows to a view by its recorded counts, never by trust."""
        response = self._call(
            "GET",
            f"/api/v1/datasets/{dataset_id}/samples",
            "a dataset samples read",
            params={"page": page, "limit": limit},
        )
        data: dict[str, Any] = _json_or_raise(response, "a dataset samples read")
        return data

    # --- capabilities --------------------------------------------------------------------------

    def openapi(self) -> dict[str, Any]:
        """miStudio's served schema at ``/api/openapi.json`` (``main.py:76``; Stage 3)."""
        response = self._call("GET", "/api/openapi.json", "the OpenAPI read")
        data: dict[str, Any] = _json_or_raise(response, "the OpenAPI read")
        return data
