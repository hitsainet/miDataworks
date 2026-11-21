"""Resolve a requested revision to a full commit (FR-001.2; 001 FTID section 3.2).

An empty ref reads the default branch's head through ``GET /api/datasets/{repo}`` (never rewritten
to ``main``); any other ref — branch, tag, short or full SHA — goes through
``/revision/{ref}``. The result's commit must be 40 lowercase hex characters or the call fails
``revision_unresolved``; it NEVER falls back to the requested ref (mutation control M1), because a
source pinned to "main" is not pinned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...clients.hf_hub import HfError, HubClient
from ...core.errors import AppError

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ResolvedRevision:
    requested_ref: str | None
    commit: str
    default_head: str | None
    card_license: Any
    tags: list[str] = field(default_factory=list)
    gated: str | None = None
    siblings: list[str] = field(default_factory=list)
    card_configs: list[str] = field(default_factory=list)


def resolve_revision(client: HubClient, repo_id: str, ref: str | None) -> ResolvedRevision:
    requested = ref.strip() if ref and ref.strip() else None
    try:
        data = client.revision(repo_id, requested) if requested else client.dataset(repo_id)
    except HfError as exc:
        if requested and exc.code == "hf_not_found" and exc.details.get("status") == 404:
            raise AppError(
                f"{repo_id} has no revision {requested!r}. Give a branch, tag or commit that exists.",
                code="revision_unresolved",
                status_code=404,
                details={"revision": requested},
            ) from None
        raise
    commit = str(data.get("sha") or "")
    if not _COMMIT.fullmatch(commit):
        raise AppError(
            f"Hugging Face did not return a full commit for {repo_id}@{requested or 'default branch'}.",
            code="revision_unresolved",
            status_code=404,
            details={"revision": requested},
        )
    card = data.get("cardData") or {}
    configs = [
        c.get("config_name")
        for c in card.get("configs", [])
        if isinstance(c, dict) and c.get("config_name")
    ]
    gated = data.get("gated")
    return ResolvedRevision(
        requested_ref=requested,
        commit=commit,
        default_head=None if requested else commit,
        card_license=card.get("license"),
        tags=list(data.get("tags") or []),
        gated=None if gated is None else str(gated).lower(),
        siblings=[s.get("rfilename", "") for s in data.get("siblings") or []],
        card_configs=[str(c) for c in configs],
    )
