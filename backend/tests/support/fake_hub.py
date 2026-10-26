"""A faithful in-memory Hugging Face Hub for publish tests (008 FTASKS 8.2; FTID 008 section 8).

It stands in for ``HfApi`` only — the real ``HubClient`` runs over it. Faithful where a mock would
agree with the code by construction:

- hashes are computed from the BYTES it receives: ``lfs.sha256`` and size for ``*.parquet``, the
  git blob SHA-1 (``blob_id``) for anything else. It never accepts a hash from the caller;
- it keeps a commit graph and refuses a ``parent_commit`` that is not the head (HTTP 412);
- ``create_repo`` makes the first commit with ``.gitattributes``, as the Hub does.

Injectable faults: ``lose_next_commit_response`` (store the commit, then raise a transport error —
EC-4), ``corrupt_path`` (report a wrong hash for one path — EC-3), ``drop_path`` (omit a file from
the listing), ``fail_reads`` (answer N reads with 429 and ``Retry-After`` — EC-5), and
``add_foreign_file`` (EC-14).
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError
from huggingface_hub.hf_api import BlobLfsInfo, RepoSibling

GITATTRIBUTES = b"*.parquet filter=lfs diff=lfs merge=lfs -text\n"
_counter = itertools.count(1)


def git_blob(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content, usedforsecurity=False).hexdigest()


def _response(status: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(
        status, headers=headers or {}, request=httpx2.Request("GET", "https://hub.test/api")
    )


@dataclass
class Commit:
    oid: str
    files: dict[str, bytes]
    title: str
    parent: str | None


@dataclass
class Repo:
    private: bool
    commits: list[Commit] = field(default_factory=list)

    @property
    def head(self) -> Commit:
        return self.commits[-1]


WRITE_TOKEN_INFO = {
    "name": "mistudio",
    "orgs": [],
    "auth": {"accessToken": {"role": "write", "displayName": "dw"}},
}


class FakeHub:
    def __init__(self) -> None:
        self.repos: dict[str, Repo] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.lose_next_commit_response = False
        self.corrupt_path: str | None = None
        self.drop_path: str | None = None
        self.fail_reads = 0
        self.received_tokens: list[str] = []

    # --- test helpers ---------------------------------------------------------------------

    def api(self, token: str | None = None) -> FakeApi:
        self.received_tokens.append(str(token))
        return FakeApi(self, token)

    def add_token(self, token: str, info: dict[str, Any] | None = None) -> None:
        self.tokens[token] = info or WRITE_TOKEN_INFO

    def add_foreign_file(self, repo_id: str, path: str, content: bytes = b"not ours\n") -> str:
        repo = self.repos.setdefault(repo_id, Repo(private=True))
        files = dict(repo.commits[-1].files) if repo.commits else {".gitattributes": GITATTRIBUTES}
        files[path] = content
        return self._append(repo, files, "someone else's commit")

    def head_files(self, repo_id: str) -> dict[str, bytes]:
        return dict(self.repos[repo_id].head.files)

    def commit_count(self, repo_id: str) -> int:
        return len(self.repos[repo_id].commits)

    def _append(self, repo: Repo, files: dict[str, bytes], title: str) -> str:
        oid = hashlib.sha1(
            f"commit-{next(_counter)}-{title}".encode(), usedforsecurity=False
        ).hexdigest()
        repo.commits.append(
            Commit(oid, files, title, repo.commits[-1].oid if repo.commits else None)
        )
        return oid


class FakeApi:
    """The subset of ``HfApi`` the publish client calls, with the same keyword names."""

    def __init__(self, hub: FakeHub, token: str | None) -> None:
        self._hub = hub
        self._token = token

    def _record(self, name: str, **kwargs: Any) -> None:
        self._hub.calls.append((name, kwargs))

    def _repo(self, repo_id: str) -> Repo:
        repo = self._hub.repos.get(repo_id)
        if repo is None:
            raise RepositoryNotFoundError("Repository Not Found", response=_response(404))
        return repo

    def _maybe_fail_read(self) -> None:
        if self._hub.fail_reads > 0:
            self._hub.fail_reads -= 1
            raise HfHubHTTPError("Too Many Requests", response=_response(429, {"Retry-After": "0"}))

    def whoami(self, token: Any = None) -> dict[str, Any]:
        self._record("whoami")
        info = self._hub.tokens.get(str(self._token))
        if info is None:
            raise HfHubHTTPError("Invalid user token", response=_response(401))
        return info

    def dataset_info(
        self, repo_id: str, *, revision: str | None = None, files_metadata: bool = False, **_: Any
    ) -> Any:
        self._record(
            "dataset_info", repo_id=repo_id, revision=revision, files_metadata=files_metadata
        )
        self._maybe_fail_read()
        repo = self._repo(repo_id)
        commit = (
            repo.head
            if revision is None
            else next((c for c in repo.commits if c.oid == revision), None)
        )
        if commit is None:
            raise HfHubHTTPError("Revision Not Found", response=_response(404))
        siblings = []
        for path, content in sorted(commit.files.items()):
            if path == self._hub.drop_path:
                continue
            if path.endswith(".parquet"):
                sha = hashlib.sha256(content).hexdigest()
                if path == self._hub.corrupt_path:
                    sha = "0" * 64
                siblings.append(
                    RepoSibling(
                        rfilename=path,
                        size=len(content),
                        blob_id=git_blob(b"lfs pointer " + sha.encode()),
                        lfs=BlobLfsInfo(size=len(content), sha256=sha, pointer_size=134),
                    )
                )
            else:
                blob = git_blob(content)
                if path == self._hub.corrupt_path:
                    blob = "0" * 40
                siblings.append(
                    RepoSibling(rfilename=path, size=len(content), blob_id=blob, lfs=None)
                )
        return SimpleNamespace(
            id=repo_id,
            private=repo.private,
            sha=commit.oid,
            siblings=siblings if files_metadata else siblings,
        )

    def create_repo(
        self,
        repo_id: str,
        *,
        private: bool | None = None,
        repo_type: str | None = None,
        exist_ok: bool = False,
        **_: Any,
    ) -> Any:
        self._record(
            "create_repo", repo_id=repo_id, private=private, repo_type=repo_type, exist_ok=exist_ok
        )
        if repo_id in self._hub.repos:
            if exist_ok:
                return SimpleNamespace(repo_id=repo_id)
            raise HfHubHTTPError("Conflict: repo exists", response=_response(409))
        repo = Repo(private=bool(private))
        self._hub.repos[repo_id] = repo
        self._hub._append(repo, {".gitattributes": GITATTRIBUTES}, "initial commit")
        return SimpleNamespace(repo_id=repo_id)

    def create_commit(
        self,
        repo_id: str,
        operations: Any,
        *,
        commit_message: str,
        repo_type: str | None = None,
        parent_commit: str | None = None,
        **kwargs: Any,
    ) -> Any:
        ops = list(operations)
        self._record(
            "create_commit",
            repo_id=repo_id,
            commit_message=commit_message,
            repo_type=repo_type,
            parent_commit=parent_commit,
            operations=ops,
            **kwargs,
        )
        repo = self._repo(repo_id)
        if parent_commit is not None and parent_commit != repo.head.oid:
            raise HfHubHTTPError(
                "Precondition Failed: parent commit is not head", response=_response(412)
            )
        files = dict(repo.head.files)
        for op in ops:
            if type(op).__name__ == "CommitOperationDelete":
                files.pop(op.path_in_repo, None)
            else:
                source = op.path_or_fileobj
                files[op.path_in_repo] = (
                    Path(source).read_bytes() if isinstance(source, str | Path) else source.read()
                )
        oid = self._hub._append(repo, files, commit_message)
        if self._hub.lose_next_commit_response:
            self._hub.lose_next_commit_response = False
            raise httpx2.ConnectError("connection reset after the commit landed")
        return SimpleNamespace(oid=oid, commit_url=f"https://hub.test/{repo_id}/commit/{oid}")

    def update_repo_settings(
        self, repo_id: str, *, private: bool | None = None, repo_type: str | None = None, **_: Any
    ) -> None:
        self._record("update_repo_settings", repo_id=repo_id, private=private, repo_type=repo_type)
        repo = self._repo(repo_id)
        if private is not None:
            repo.private = private

    def list_repo_commits(
        self, repo_id: str, *, repo_type: str | None = None, **_: Any
    ) -> list[Any]:
        self._record("list_repo_commits", repo_id=repo_id)
        repo = self._repo(repo_id)
        return [
            SimpleNamespace(commit_id=c.oid, title=c.title, message=c.title)
            for c in reversed(repo.commits)
        ]

    def hf_hub_download(
        self,
        repo_id: str,
        filename: str,
        *,
        repo_type: str | None = None,
        revision: str | None = None,
        cache_dir: Any = None,
        **_: Any,
    ) -> str:
        self._record("hf_hub_download", repo_id=repo_id, filename=filename, revision=revision)
        repo = self._repo(repo_id)
        commit = next(c for c in repo.commits if c.oid == revision) if revision else repo.head
        target = Path(cache_dir) / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(commit.files[filename])
        return str(target)
