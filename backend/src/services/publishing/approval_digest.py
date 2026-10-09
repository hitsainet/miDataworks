"""The publish request digest an approval binds to (FR-008.51, FR-008.66; FTDD 008 section 5.5).

``sha256(canonical_json({action, version_id, build_id, files: [[path, sha256]…], repo_id,
visibility, card_prose_sha256}))``. A changed visibility, card or build after approval changes the
digest, so the approval no longer covers the request (EC-15). 009's send approval lists one such
digest per role publish (P-06).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..identity import bytes_sha256, digest_text


def publish_request_digest(
    *,
    kind: str,
    version_id: str,
    build_id: str,
    build_files: Sequence[Mapping[str, Any]],
    repo_id: str,
    visibility: str,
    card_prose: str,
) -> str:
    return digest_text(
        {
            "action": "hub_push",
            "kind": kind,
            "version_id": version_id,
            "build_id": build_id,
            "files": sorted([str(f["path"]), str(f["sha256"])] for f in build_files),
            "repo_id": repo_id,
            "visibility": visibility,
            "card_prose_sha256": bytes_sha256(card_prose.encode("utf-8")),
        }
    )
