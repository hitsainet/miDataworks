# Origin (fragment): miStudio (Onegaishimas/miStudio) backend/src/services/probe_definition_publisher.py
# @ c829a2cc, `_redact`. Mode: adapt (docs/REUSE.md): the publisher's rule that nothing derived
# from a Hub exception may carry the token or the request. Changed: exceptions are rebuilt from
# the status code and a fixed message instead of a redacted copy of the original text, so there
# is nothing to redact; `__cause__` is suppressed so the request object is never chained.
"""The Hub client for publishing (FR-008.17–FR-008.24; FTID 008 section 11).

The ONLY module in ``src/`` that constructs ``HfApi`` (``test_publish_wiring_ast.py``). It takes
the token as an argument from the publish worker — the only code that decrypts it — and keeps
only the ``HfApi`` object built from it, never the string. ``repr`` says ``<redacted>``. Every
Hub exception is rebuilt as :class:`HubError` from its status code and a fixed message, raised
``from None``: the original carries the request (and so its ``Authorization`` header).

Retries (EC-5): a 429 or 5xx on a READ is retried up to ``PUBLISH_HUB_RETRIES`` times, honouring
``Retry-After``, calling ``on_wait`` before each sleep so the job keeps its heartbeat. A COMMIT is
never retried here: a lost response is recovered by searching the history for the commit's
marker (FTDD 008 section 5.4), so a commit that landed is never pushed twice.
"""

from __future__ import annotations

import logging
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from httpx2 import HTTPError
from huggingface_hub import CommitOperationAdd, CommitOperationDelete, HfApi
from huggingface_hub.errors import HfHubHTTPError, RepositoryNotFoundError

from ..services.publishing.hub_plan import Plan, RemoteFile, RepoState

logger = logging.getLogger(__name__)

TokenScope = Literal["write", "read", "invalid", "cannot_verify", "namespace_denied"]
REPO_TYPE = "dataset"


class HubError(Exception):
    """A Hub failure, carrying no token, header or request."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retry_after = retry_after

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "status": self.status}


def _retry_after(exc: HfHubHTTPError) -> float | None:
    value = exc.response.headers.get("Retry-After") if exc.response is not None else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def wrap(exc: BaseException, what: str) -> HubError:
    """Rebuild a Hub exception without its request. Never ``str(exc)``."""
    if isinstance(exc, HubError):
        return exc
    if isinstance(exc, RepositoryNotFoundError):
        return HubError(
            "hub_not_found", f"{what}: the Hub has no such repository for this token.", status=404
        )
    if isinstance(exc, HfHubHTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status in (409, 412):
            return HubError(
                "repo_head_moved",
                f"{what}: the repository changed since its state was read. Run checks again.",
                status=status,
            )
        if status == 401:
            return HubError("token_rejected", f"{what}: the Hub rejected the token.", status=401)
        if status == 403:
            return HubError("token_cannot_write", f"{what}: the token may not do this.", status=403)
        if status == 429 or (status is not None and status >= 500):
            return HubError(
                "hub_unavailable",
                f"{what}: the Hub answered {status}.",
                status=status,
                retry_after=_retry_after(exc),
            )
        return HubError("hub_error", f"{what}: the Hub answered {status}.", status=status)
    if isinstance(exc, HTTPError | OSError | TimeoutError):
        return HubError("hub_transport", f"{what}: the connection failed ({type(exc).__name__}).")
    return HubError("hub_error", f"{what}: unexpected {type(exc).__name__}.")


class HubClient:
    def __init__(
        self,
        token: str,
        *,
        api_factory: Callable[..., Any] = HfApi,
        retries: int = 5,
        backoff_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
        on_wait: Callable[[str], None] | None = None,
    ) -> None:
        self._api = api_factory(token=token)
        self._retries = retries
        self._backoff = backoff_s
        self._sleep = sleep
        self._on_wait = on_wait

    def __repr__(self) -> str:
        return "HubClient(token=<redacted>)"

    __str__ = __repr__

    # --- plumbing ---------------------------------------------------------------------------

    def _read(self, what: str, call: Callable[[], Any]) -> Any:
        attempt = 0
        while True:
            try:
                return call()
            except Exception as exc:  # noqa: BLE001 - rebuilt below without the request
                error = wrap(exc, what)
            if error.code not in ("hub_unavailable", "hub_transport") or attempt >= self._retries:
                raise error from None
            wait = (
                error.retry_after if error.retry_after is not None else self._backoff * (2**attempt)
            )
            attempt += 1
            logger.info("Hub %s: %s; retry %d in %.1f s", what, error.code, attempt, wait)
            if self._on_wait is not None:
                self._on_wait(
                    f"Waiting for the Hub ({error.code}); retry {attempt} of {self._retries}."
                )
            self._sleep(wait)

    # --- token ------------------------------------------------------------------------------

    def whoami_scope(self, repo_id: str) -> TokenScope:
        """C-2 (FR-008.16). Unknown layouts fail closed as ``cannot_verify``.

        The ``auth.accessToken`` layout is the one ``huggingface_hub/_login.py`` reads (``role``).
        For a ``fineGrained`` token the scoped permissions are read from
        ``auth.accessToken.fineGrained.scoped[].{entity.name, permissions}``; that layout is
        to be confirmed with a real fine-grained token (FTASKS 1.2, operator session) and anything
        else refuses.
        """
        try:
            info = self._read("whoami", lambda: self._api.whoami())
        except HubError as exc:
            return "invalid" if exc.code == "token_rejected" else "cannot_verify"
        namespace = repo_id.split("/", 1)[0]
        token_info = ((info.get("auth") or {}).get("accessToken")) or {}
        role = token_info.get("role")
        names = {str(info.get("name"))} | {
            str(o.get("name")) for o in info.get("orgs") or [] if isinstance(o, dict)
        }
        if role == "read":
            return "read"
        if role == "write":
            return "write" if namespace in names else "namespace_denied"
        if role == "fineGrained":
            fine = token_info.get("fineGrained") or {}
            scoped = fine.get("scoped")
            if not isinstance(scoped, list):
                return "cannot_verify"
            for entry in scoped:
                entity = (entry or {}).get("entity") or {}
                perms = (entry or {}).get("permissions") or []
                if entity.get("name") == namespace and "repo.write" in perms:
                    return "write"
            return "namespace_denied"
        return "cannot_verify"

    # --- reads ------------------------------------------------------------------------------

    @staticmethod
    def _remote(siblings: Sequence[Any]) -> tuple[RemoteFile, ...]:
        files: list[RemoteFile] = []
        for s in siblings:
            lfs = s.lfs
            files.append(
                RemoteFile(
                    path=s.rfilename,
                    size=(lfs.size if lfs is not None else s.size),
                    blob_id=s.blob_id,
                    lfs_sha256=(lfs.sha256 if lfs is not None else None),
                )
            )
        return tuple(files)

    def read_state(self, repo_id: str) -> RepoState:
        try:
            info = self._read(
                "read repository state",
                lambda: self._api.dataset_info(repo_id, files_metadata=True),
            )
        except HubError as exc:
            if exc.code == "hub_not_found":
                return RepoState(exists=False, private=None, head=None)
            raise
        return RepoState(True, bool(info.private), info.sha, self._remote(info.siblings or []))

    def siblings(self, repo_id: str, revision: str) -> tuple[RemoteFile, ...]:
        info = self._read(
            "read files at commit",
            lambda: self._api.dataset_info(repo_id, revision=revision, files_metadata=True),
        )
        return self._remote(info.siblings or [])

    def download(self, repo_id: str, path: str, revision: str) -> bytes:
        with tempfile.TemporaryDirectory() as scratch:
            local = self._read(
                f"download {path}",
                lambda: self._api.hf_hub_download(
                    repo_id, path, repo_type=REPO_TYPE, revision=revision, cache_dir=scratch
                ),
            )
            return Path(local).read_bytes()

    def is_private(self, repo_id: str) -> bool:
        info = self._read("read visibility", lambda: self._api.dataset_info(repo_id))
        return bool(info.private)

    def find_commit_by_marker(self, repo_id: str, marker: str, since: str | None) -> str | None:
        commits = self._read(
            "read history", lambda: self._api.list_repo_commits(repo_id, repo_type=REPO_TYPE)
        )
        for c in commits:  # newest first
            if since is not None and c.commit_id == since:
                return None
            if marker in (c.title or "") or marker in (c.message or ""):
                return str(c.commit_id)
        return None

    # --- writes -----------------------------------------------------------------------------

    def create_private_repo(self, repo_id: str) -> None:
        try:
            self._api.create_repo(repo_id, private=True, repo_type=REPO_TYPE, exist_ok=False)
        except Exception as exc:  # noqa: BLE001 - rebuilt without the request
            raise wrap(exc, "create repository") from None

    def commit(self, repo_id: str, plan: Plan, message: str) -> str:
        operations: list[Any] = [
            CommitOperationAdd(path_in_repo=f.path, path_or_fileobj=f.local_path) for f in plan.adds
        ]
        operations += [CommitOperationDelete(path_in_repo=p) for p in plan.deletes]
        try:
            info = self._api.create_commit(
                repo_id,
                operations,
                commit_message=message,
                repo_type=REPO_TYPE,
                parent_commit=plan.parent_commit,
            )
        except Exception as exc:  # noqa: BLE001 - rebuilt without the request
            raise wrap(exc, "commit") from None
        return str(info.oid)

    def set_public(self, repo_id: str) -> None:
        try:
            self._api.update_repo_settings(repo_id, private=False, repo_type=REPO_TYPE)
        except Exception as exc:  # noqa: BLE001 - rebuilt without the request
            raise wrap(exc, "make public") from None
