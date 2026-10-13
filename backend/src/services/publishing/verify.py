"""Verification after the push (FR-008.21, FR-008.22, FR-008.24, FR-008.64; FTDD 008 section 5.4).

Pure comparisons over what the Hub reports at the new commit:

1. the commit's path set must equal the built paths plus ``.gitattributes`` — a missing file or an
   extra file fails (the whole tree is verified, not only the files built);
2. every built file is compared with the Hub's own hash: ``lfs.sha256`` (and size) when the Hub
   stored it in LFS, else ``blob_id`` against the local git blob SHA-1. Small files are never
   skipped — miStudio's defect was in a JSON file;
3. the card read back at the commit must map exactly the split names to their paths in
   ``configs`` (:func:`check_configs`).

Any failure → ``verification_failed``; the caller leaves the repository private.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .card import CardError, read_front_matter
from .hub_plan import HUB_DEFAULTS, BuiltFile, RemoteFile


@dataclass(frozen=True)
class FileResult:
    path: str
    role: str
    split: str | None
    bytes: int
    sha256: str
    git_blob_sha1: str
    remote_lfs_sha256: str | None
    remote_blob_id: str | None
    remote_size: int | None
    match: bool


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    files: list[FileResult]
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    configs_ok: bool = True
    configs_problem: str | None = None

    @property
    def mismatched(self) -> list[str]:
        return [f.path for f in self.files if not f.match]

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files_checked": len(self.files),
            "mismatched": self.mismatched,
            "missing": self.missing,
            "extra": self.extra,
            "configs_ok": self.configs_ok,
            "configs_problem": self.configs_problem,
        }


def compare(built: Sequence[BuiltFile], remote: Sequence[RemoteFile]) -> VerificationResult:
    by_path = {r.path: r for r in remote}
    expected = {f.path for f in built}
    missing = sorted(expected - set(by_path))
    extra = sorted(set(by_path) - expected - HUB_DEFAULTS)
    results: list[FileResult] = []
    for f in built:
        r = by_path.get(f.path)
        if r is None:
            match = False
        elif r.lfs_sha256 is not None:
            match = r.lfs_sha256 == f.sha256 and (r.size is None or r.size == f.bytes)
        else:
            match = r.blob_id is not None and r.blob_id == f.git_blob_sha1
        results.append(
            FileResult(
                path=f.path,
                role=f.role,
                split=f.split,
                bytes=f.bytes,
                sha256=f.sha256,
                git_blob_sha1=f.git_blob_sha1,
                remote_lfs_sha256=r.lfs_sha256 if r else None,
                remote_blob_id=r.blob_id if r else None,
                remote_size=r.size if r else None,
                match=match,
            )
        )
    ok = not missing and not extra and all(x.match for x in results)
    return VerificationResult(ok, results, missing, extra)


def check_configs(card: bytes, splits: Mapping[str, str]) -> tuple[bool, str | None]:
    """The card's ``configs[0].data_files`` maps exactly ``{split: path}`` (FR-008.24)."""
    try:
        fm = read_front_matter(card)
        files = fm["configs"][0]["data_files"]
        mapping = {str(x["split"]): str(x["path"]) for x in files}
    except (CardError, KeyError, IndexError, TypeError) as exc:
        return False, f"the card read back has no usable configs block ({exc})"
    if mapping != dict(splits):
        return False, f"configs maps {mapping}, the build has {dict(splits)}"
    return True, None


def with_configs(result: VerificationResult, ok: bool, problem: str | None) -> VerificationResult:
    return VerificationResult(
        ok=result.ok and ok,
        files=result.files,
        missing=result.missing,
        extra=result.extra,
        configs_ok=ok,
        configs_problem=problem,
    )
