"""Route-existence capabilities read from miStudio's SERVED OpenAPI (FR-009.84; FTDD 009 section
5.6; ADR-027).

Read at most once per ``DETECTOR_CAPABILITY_TTL_SECONDS`` per miStudio URL, and at send start:

- XR-1: is ``dataset_version_manifest`` a property of ``ProbeDatasetCreate``?
- XR-3: is ``revision`` a property of ``DatasetDownloadRequest``?
- XR-2: is a per-row evaluation score route served? Its path is not named anywhere yet (miStudio
  BRD-MIS-DATAWORKS-002 seed), so the probe looks for a GET path under
  ``/api/v1/probe-monitors/probes/`` ending in ``/scores`` (plural). The offline one-input scorer
  ``…/probes/{probe_id}/score`` is NOT that route and never counts.

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


@dataclass(frozen=True)
class Capabilities:
    url: str
    dataset_version_manifest: bool
    download_revision: bool
    per_row_scores: bool
    read_at: float

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = {
            name: (
                "served"
                if getattr(self, name)
                else f"not served by miStudio at {self.url} (read {OPENAPI_PATH})"
            )
            for name in ("dataset_version_manifest", "download_revision", "per_row_scores")
        }
        return out


_CACHE: dict[str, Capabilities] = {}


def _schema_properties(document: dict[str, Any], name: str) -> set[str]:
    schema = (document.get("components") or {}).get("schemas", {}).get(name) or {}
    return set((schema.get("properties") or {}).keys())


def from_openapi(url: str, document: dict[str, Any]) -> Capabilities:
    """Pure: the three capabilities a served schema document states."""
    paths = document.get("paths") or {}
    per_row = any(
        p.startswith("/api/v1/probe-monitors/probes/")
        and p.rstrip("/").endswith("/scores")
        and "get" in (paths[p] or {})
        for p in paths
    )
    return Capabilities(
        url=url,
        dataset_version_manifest="dataset_version_manifest"
        in _schema_properties(document, "ProbeDatasetCreate"),
        download_revision="revision" in _schema_properties(document, "DatasetDownloadRequest"),
        per_row_scores=per_row,
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
