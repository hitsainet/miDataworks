"""Route-existence capabilities read from miStudio's SERVED OpenAPI (FR-009.84; FTDD 009 section
5.6; ADR-027).

Read at most once per ``DETECTOR_CAPABILITY_TTL_SECONDS`` per miStudio URL, and at send start:

- XR-1: is ``dataset_version_manifest`` a property of ``ProbeDatasetCreate``?
- XR-3: is ``revision`` a property of ``DatasetDownloadRequest``?
- XR-2: is a per-row evaluation score route served? miStudio 035 names it
  ``/api/v1/probe-monitors/probes/{probe_id}/evaluations/{evaluation_id}/scores``; the probe looks for
  a GET path under ``/api/v1/probe-monitors/probes/`` ending in ``/scores`` (plural). The offline
  one-input scorer ``…/probes/{probe_id}/score`` is NOT that route and never counts.
- XR-4 (feature 011, D39): does ``GET /api/v1/probe-monitors/runs`` take a ``train_dataset_id`` query
  parameter (``runs_train_filter``)? Without it, results scan the newest 200 runs and say so.

The names are owned by miStudio (BRD-MIS-DATAWORKS-002 section 6) and pinned here as constants; a
test pins the same strings against the fake miStudio's document (FR-011.2).

The schema is read at ``{base}/api/openapi.json`` — miStudio serves it there (``main.py:76``); a read
of ``/openapi.json`` reaches the frontend and would report "not served" forever (Stage 3). A body
that is not JSON raises ``mistudio_not_json``: an unreadable schema is never read as "not served".
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from ...clients.mistudio_client import MiStudioClient
from ...core.config import get_settings

OPENAPI_PATH = "/api/openapi.json"

#: The names miStudio 035 owns (BRD-MIS-DATAWORKS-002 section 6), read exactly as written here.
MANIFEST_SCHEMA = "ProbeDatasetCreate"
MANIFEST_PROPERTY = "dataset_version_manifest"
DOWNLOAD_SCHEMA = "DatasetDownloadRequest"
REVISION_PROPERTY = "revision"
SCORES_PREFIX = "/api/v1/probe-monitors/probes/"
SCORES_SUFFIX = "/scores"
RUNS_PATH = "/api/v1/probe-monitors/runs"
RUN_FILTER_PARAM = "train_dataset_id"

NAMES = ("dataset_version_manifest", "download_revision", "per_row_scores", "runs_train_filter")


@dataclass(frozen=True)
class Capabilities:
    url: str
    dataset_version_manifest: bool
    download_revision: bool
    per_row_scores: bool
    runs_train_filter: bool
    read_at: float

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = {
            name: (
                "served"
                if getattr(self, name)
                else f"not served by miStudio at {self.url} (read {OPENAPI_PATH})"
            )
            for name in NAMES
        }
        return out


_CACHE: dict[str, Capabilities] = {}


def _schema_properties(document: dict[str, Any], name: str) -> set[str]:
    schema = (document.get("components") or {}).get("schemas", {}).get(name) or {}
    return set((schema.get("properties") or {}).keys())


def _query_parameters(document: dict[str, Any], path: str) -> set[str]:
    """Names of the ``in: query`` parameters of ``GET path`` (none when the path is absent)."""
    operation = ((document.get("paths") or {}).get(path) or {}).get("get") or {}
    return {
        str(p.get("name"))
        for p in operation.get("parameters") or []
        if isinstance(p, dict) and p.get("in") == "query"
    }


def from_openapi(url: str, document: dict[str, Any]) -> Capabilities:
    """Pure: the four capabilities a served schema document states."""
    paths = document.get("paths") or {}
    per_row = any(
        p.startswith(SCORES_PREFIX)
        and p.rstrip("/").endswith(SCORES_SUFFIX)
        and "get" in (paths[p] or {})
        for p in paths
    )
    return Capabilities(
        url=url,
        dataset_version_manifest=MANIFEST_PROPERTY in _schema_properties(document, MANIFEST_SCHEMA),
        download_revision=REVISION_PROPERTY in _schema_properties(document, DOWNLOAD_SCHEMA),
        per_row_scores=per_row,
        runs_train_filter=RUN_FILTER_PARAM in _query_parameters(document, RUNS_PATH),
        read_at=time.time(),
    )


def read(client: MiStudioClient, *, fresh: bool = False) -> Capabilities:
    """The cached capabilities of ``client``'s miStudio, re-read after the TTL or when ``fresh``."""
    ttl = get_settings().detector_capability_ttl_seconds
    cached = _CACHE.get(client.base_url)
    if not fresh and cached is not None and time.time() - cached.read_at < ttl:
        return cached
    caps = from_openapi(client.base_url, client.openapi())
    _CACHE[client.base_url] = caps
    return caps


def clear_cache() -> None:
    _CACHE.clear()
