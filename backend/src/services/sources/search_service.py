"""Find candidate Hugging Face datasets before importing one (PII run G2, 2026-10-20).

The run found its PII datasets with curl against the Hub API: ``dataworks_preview_hf_dataset``
previews one known repository, and there was no search. This runs in a worker like the preview,
because the stored Hugging Face token is decrypted only there; it is sent to the Hub only (as the
preview sends it) and never returned, logged or written. Each result carries what the next
decision needs: licence, size category, downloads, gating and whether it is private.
"""

from __future__ import annotations

from typing import Any

from ...clients.hf_hub import HubClient

#: Licence filters accepted in one search (one Hub request each).
MAX_LICENCES = 5


def _tags(item: dict[str, Any], prefix: str) -> list[str]:
    return [t[len(prefix) :] for t in item.get("tags") or [] if str(t).startswith(prefix)]


def summarise(item: dict[str, Any]) -> dict[str, Any]:
    # The Hub's own license:<id> tags; the card itself is read only by revision.py (one reader).
    licences = _tags(item, "license:")
    return {
        "repo_id": item.get("id"),
        "licence": licences[0] if licences else None,
        "licences": licences,
        "downloads": item.get("downloads"),
        "likes": item.get("likes"),
        "last_modified": item.get("lastModified"),
        "gated": item.get("gated") if item.get("gated") is not None else False,
        "private": bool(item.get("private")),
        "size_category": next(iter(_tags(item, "size_categories:")), None),
        "task_categories": _tags(item, "task_categories:"),
        "languages": _tags(item, "language:"),
        "description": (str(item.get("description") or "")[:300]) or None,
    }


def search(client: HubClient, request: dict[str, Any]) -> dict[str, Any]:
    query = str(request["query"])
    limit = int(request["limit"])
    licences = list(request.get("licences") or [])[:MAX_LICENCES] or [None]
    seen: dict[str, dict[str, Any]] = {}
    for licence in licences:
        for item in client.search_datasets(query, licence, limit):
            summary = summarise(item)
            if summary["repo_id"] and summary["repo_id"] not in seen:
                seen[str(summary["repo_id"])] = summary
    items = sorted(seen.values(), key=lambda s: (-(s["downloads"] or 0), str(s["repo_id"])))
    return {
        "query": query,
        "licences": [lic for lic in licences if lic],
        "items": items[:limit],
        "total": min(len(items), limit),
        "next_step": "dataworks_preview_hf_dataset(repo_id) shows splits, columns and sample "
        "rows; import it, then dataworks_profile_source gives whole-split statistics.",
    }
