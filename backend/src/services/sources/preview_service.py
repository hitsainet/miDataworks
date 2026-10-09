"""Preview a Hugging Face dataset before importing it (FR-001.13, 001.15–001.18; FTDD §6.3).

Runs in a worker (the stored token is decrypted only there). Returns the commit an import would
pin, a note when the viewer's sample comes from a different commit (the viewer reads the branch
head), configs (never one chosen silently), splits with rows and bytes, columns, up to 100 sample
rows with long cells truncated and marked, the licence, gated status, size and detection on the
sample. A viewer that cannot answer adds an ``unavailable`` entry; the Hub facts still come back.

A preview creates no row, no job record and no file.
"""

from __future__ import annotations

from typing import Any

from ...clients.hf_hub import HubClient, ViewerUnavailable
from .config_choice import check_split, choose_config
from .detection import detect
from .licence import licence_from_hub
from .revision import resolve_revision

SAMPLE_LIMIT = 100
CELL_LIMIT = 2000


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > CELL_LIMIT:
        return {"truncated": True, "text": value[:CELL_LIMIT], "length": len(value)}
    return value


def build_preview(client: HubClient, request: dict[str, Any]) -> dict[str, Any]:
    repo = request["repo_id"]
    resolved = resolve_revision(client, repo, request.get("revision"))
    unavailable: list[dict[str, str]] = []
    head = resolved.default_head
    if head is None:
        try:
            head = str(client.dataset(repo).get("sha") or "") or None
        except Exception:  # noqa: BLE001 - the head is a note, not a requirement
            head = None
    try:
        config_splits = client.viewer_splits(repo)
    except ViewerUnavailable as exc:
        config_splits = []
        unavailable.append({"part": exc.part, "reason": exc.reason})
    configs = list(dict.fromkeys([cs.config for cs in config_splits] or resolved.card_configs))
    config = choose_config(configs, request.get("config"))
    splits = [cs.split for cs in config_splits if cs.config == config]
    split = check_split(splits, request.get("split"))
    size: dict[str, Any] | None = None
    split_sizes: dict[str, dict[str, Any]] = {}
    try:
        size_body = client.viewer_size(repo, config)
        size = size_body.get("size", {}).get("config") or size_body.get("size", {}).get("dataset")
        for s in size_body.get("size", {}).get("splits", []):
            split_sizes[s["split"]] = {
                "rows": s.get("num_rows"),
                "bytes": s.get("num_bytes_parquet_files"),
            }
    except ViewerUnavailable as exc:
        unavailable.append({"part": exc.part, "reason": exc.reason})
    columns: list[dict[str, str]] = []
    sample: list[dict[str, Any]] = []
    sample_split = split or (splits[0] if splits else None)
    if config and sample_split:
        try:
            body = client.viewer_first_rows(repo, config, sample_split)
            columns = [
                {
                    "name": f["name"],
                    "type": str(
                        f.get("type", {}).get("dtype") or f.get("type", {}).get("_type") or ""
                    ),
                }
                for f in body.get("features", [])
            ]
            sample = [r.get("row", {}) for r in body.get("rows", [])[:SAMPLE_LIMIT]]
        except ViewerUnavailable as exc:
            unavailable.append({"part": exc.part, "reason": exc.reason})
    raw, display, origin = licence_from_hub(resolved.card_license, resolved.tags)
    detection = (
        detect([(c["name"], c["type"]) for c in columns], sample).as_dict() if columns else None
    )
    note = None
    if head and head != resolved.commit:
        note = "The sample comes from the branch head; the import pins the commit above."
    return {
        "repo_id": repo,
        "requested_ref": resolved.requested_ref,
        "resolved_commit": resolved.commit,
        "head_commit": head,
        "viewer_commit_note": note,
        "configs": configs,
        "config": config,
        "splits": [
            {"name": s, **split_sizes.get(s, {"rows": None, "bytes": None})} for s in splits
        ],
        "split": split,
        "columns": columns,
        "sample_rows": [{k: _truncate(v) for k, v in row.items()} for row in sample],
        "licence": {"raw": raw, "display": display, "origin": origin},
        "gated": resolved.gated,
        "size": size,
        "detection": detection,
        "unavailable": unavailable,
    }
