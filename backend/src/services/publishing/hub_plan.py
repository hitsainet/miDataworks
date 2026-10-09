"""From the repository's state and the files built: operations, "no change", or a refusal
(FR-008.64, FR-008.65; FTID 008 section 3.5). Pure.

- an oversize split is refused before any Hub call (EC-13; T-41: v1 never shards);
- a file at head that miDataworks did not write is refused, naming it (EC-14; T-42). "Written by
  miDataworks" means recorded in ``dw_publish_files`` for this repository; ``.gitattributes`` is
  the Hub's own default and allowed;
- identical digests for every target path, and nothing stale → ``no_change`` (EC-8: no empty
  commit);
- otherwise adds for every built file and deletes for stale miDataworks paths, against
  ``parent_commit`` = the head read with the state (a moved head makes the Hub refuse).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

HUB_DEFAULTS: frozenset[str] = frozenset({".gitattributes"})


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int | None
    blob_id: str | None
    lfs_sha256: str | None


@dataclass(frozen=True)
class RepoState:
    exists: bool
    private: bool | None
    head: str | None
    files: tuple[RemoteFile, ...] = ()


@dataclass(frozen=True)
class BuiltFile:
    path: str
    local_path: str
    role: str  # split | card | manifest
    split: str | None
    bytes: int
    sha256: str
    git_blob_sha1: str


@dataclass(frozen=True)
class Plan:
    adds: tuple[BuiltFile, ...]
    deletes: tuple[str, ...]
    parent_commit: str | None
    no_change: bool = False


@dataclass(frozen=True)
class Refusal:
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


def remote_matches(local: BuiltFile, remote: RemoteFile) -> bool:
    if remote.lfs_sha256 is not None:
        return remote.lfs_sha256 == local.sha256 and (remote.size in (None, local.bytes))
    return remote.blob_id == local.git_blob_sha1


def plan(
    state: RepoState,
    built: Sequence[BuiltFile],
    *,
    max_bytes: int,
    prior_dw_paths: Collection[str],
) -> Plan | Refusal:
    for f in built:
        if f.role == "split" and f.bytes > max_bytes:
            return Refusal(
                "split_too_large",
                f"Split {f.split!r} is {f.bytes:,} bytes, above the {max_bytes:,}-byte per-file "
                "limit. v1 writes one file per split; reduce the split with a recipe step.",
                {"split": f.split, "bytes": f.bytes, "limit": max_bytes},
            )
    targets = {f.path for f in built}
    remote = {r.path: r for r in state.files} if state.exists else {}
    foreign = sorted(
        p for p in remote if p not in HUB_DEFAULTS and p not in prior_dw_paths and p not in targets
    )
    # A target path occupied by a file miDataworks never wrote is foreign too: overwriting it
    # would silently replace someone else's file.
    foreign += sorted(p for p in remote if p in targets and p not in prior_dw_paths)
    if foreign:
        return Refusal(
            "repo_has_foreign_files",
            f"The repository holds {len(foreign)} file(s) miDataworks did not write, first "
            f"{foreign[0]!r}. Publish to a new repository, or remove them on the Hub first.",
            {"paths": foreign},
        )
    stale = tuple(sorted(p for p in remote if p in prior_dw_paths and p not in targets))
    unchanged = all(p in remote and remote_matches(f, remote[p]) for f in built for p in [f.path])
    if state.exists and unchanged and not stale:
        return Plan((), (), state.head, no_change=True)
    return Plan(tuple(built), stale, state.head if state.exists else None)
