"""The publish job's body (FTDD 008 section 2.2 step 4; FTID 008 sections 3.6, 7.3).

Called by ``workers/publish_tasks.py`` — the only code that decrypts the token — with a
:class:`HubClient` built from it (or ``None`` when no token is stored). Phases, in this order, each
protecting the next:

1. ``checking``: read the repository state, then re-run C-1..C-7 HERE (a prior check run is a
   preview and never trusted), snapshot them onto the publish, and refuse on any refused outcome
   or, for a public push, any amber one (``push_allowed``). An existing PUBLIC repository makes
   the push public whatever was requested (EC-1);
2. render the in-repository manifest and the card into ``publish/<publish_id>/``; plan against
   the head: oversize split or foreign file → refused, identical digests → ``no_change``;
3. ``uploading``: create the repository PRIVATE if absent; one commit (adds and stale deletes)
   with ``parent_commit``; a lost response is recovered by the commit marker, never a blind retry;
4. ``verifying``: the Hub's hash for every file and the exact path set at the new commit, and the
   card's ``configs`` read back; any failure → ``verification_failed`` and the repository stays
   private;
5. only then, for a public request with every check green, flip to public and re-read
   ``private``; record the commit, the per-file evidence and the published manifest.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.storage import atomic_write_bytes, publish_dir
from ...models.publish import (
    FileRole,
    Publish,
    PublishBuild,
    PublishFile,
    PublishStatus,
)
from ...models.version import Version
from . import card as card_mod
from .build_service import build_file_path
from .check_inputs import any_forbids, assemble, manifest_source
from .checks import CheckOutcome, TokenScope, evaluate_checks, push_allowed
from .digests import bytes_git_blob_sha1, bytes_sha256
from .hub_plan import BuiltFile, Plan, Refusal, RemoteFile, RepoState, plan
from .licence_table import TABLE
from .manifest_builder import (
    MANIFEST_FILE,
    build_document,
    dataset_target,
    manifest_bytes,
    rows_content,
    timestamp,
    with_publication,
)
from .verify import VerificationResult, check_configs, compare, with_configs

logger = logging.getLogger(__name__)

CARD_FILE = "README.md"


class Hub(Protocol):
    def whoami_scope(self, repo_id: str) -> Any: ...
    def read_state(self, repo_id: str) -> RepoState: ...
    def siblings(self, repo_id: str, revision: str) -> tuple[RemoteFile, ...]: ...
    def download(self, repo_id: str, path: str, revision: str) -> bytes: ...
    def is_private(self, repo_id: str) -> bool: ...
    def find_commit_by_marker(self, repo_id: str, marker: str, since: str | None) -> str | None: ...
    def create_private_repo(self, repo_id: str) -> None: ...
    def commit(self, repo_id: str, plan: Plan, message: str) -> str: ...
    def set_public(self, repo_id: str) -> None: ...


def marker(publish_id: str) -> str:
    return f"[dw-publish {publish_id}]"


def prior_dw_paths(session: Session, repo_id: str, exclude: str) -> set[str]:
    """Paths miDataworks wrote to this repository in earlier publishes (FR-008.64)."""
    rows = session.execute(
        select(PublishFile.path_in_repo)
        .join(Publish, Publish.id == PublishFile.publish_id)
        .where(Publish.repo_id == repo_id, Publish.id != exclude)
    ).scalars()
    return set(rows)


def recorded_commits(session: Session, repo_id: str) -> dict[str, str]:
    """version_id -> the commit its latest successful publish to this repository made."""
    rows = session.execute(
        select(Publish.version_id, Publish.commit)
        .where(Publish.repo_id == repo_id, Publish.status == PublishStatus.PUBLISHED)
        .order_by(Publish.completed_at)
    ).all()
    return {str(v): str(c) for v, c in rows if c}


def split_files(build: PublishBuild) -> list[BuiltFile]:
    assert build.files is not None
    return [
        BuiltFile(
            path=f["path"],
            local_path=str(build_file_path(build, f["path"])),
            role=FileRole.SPLIT,
            split=f["name"],
            bytes=int(f["bytes"]),
            sha256=f["sha256"],
            git_blob_sha1=f["git_blob_sha1"],
        )
        for f in build.files
    ]


def _write(publish_id: str, name: str, content: bytes, role: str) -> BuiltFile:
    path = atomic_write_bytes(publish_dir(publish_id) / name, content)
    return BuiltFile(
        path=name,
        local_path=str(path),
        role=role,
        split=None,
        bytes=len(content),
        sha256=bytes_sha256(content),
        git_blob_sha1=bytes_git_blob_sha1(content),
    )


def version_identity(version: Version, dataset_name: str) -> dict[str, Any]:
    return {
        "dataset": dataset_name,
        "version_id": version.id,
        "number": version.number,
        "parent_version_id": version.parent_version_id,
        "created_at": timestamp(version.created_at),
        "created_by_origin": version.created_by_origin,
        "recipe": {"format": "dw.recipe/v1", "sha256": version.recipe_hash},
        "seed": int(version.seed),
        "row_key_scheme": version.rowkey_scheme,
    }


class _Stop(Exception):
    def __init__(self, status: str, error: dict[str, Any] | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.error = error


def _update(session: Session, pub: Publish, **values: Any) -> None:
    for key, value in values.items():
        setattr(pub, key, value)
    session.commit()


def run_publish(
    session: Session,
    publish_id: str,
    hub: Hub | None,
    *,
    cancel_requested: Callable[[], bool],
    progress: Callable[[str, float], None],
) -> dict[str, Any]:
    pub = session.get(Publish, publish_id)
    assert pub is not None
    build = session.get(PublishBuild, pub.build_id)
    version = session.get(Version, pub.version_id)
    assert build is not None and version is not None and build.files is not None
    timings: dict[str, float] = {}
    started = time.monotonic()
    settings = get_settings()
    try:
        _update(session, pub, status=PublishStatus.CHECKING)
        progress("checking", 5.0)
        token_scope: TokenScope = "missing" if hub is None else hub.whoami_scope(pub.repo_id)
        state = (
            hub.read_state(pub.repo_id)
            if hub is not None and token_scope != "invalid"  # noqa: S105
            else RepoState(False, None, None)
        )
        effective = pub.requested_visibility
        if state.exists and state.private is False:
            effective = "public"  # EC-1: an existing public repository makes the push public
        assembled = assemble(
            session,
            version,
            label_column=build.projection["label_column"],
            visibility=effective,
            token_scope=token_scope,
        )
        outcomes = evaluate_checks(assembled.inputs)
        ok, blocking = push_allowed(outcomes, effective)
        _update(
            session,
            pub,
            check_snapshot=[o.as_dict() for o in outcomes],
            licence_table_version=TABLE.version,
            repo_existed=state.exists,
            repo_was_private=state.private,
            head_before=state.head,
        )
        timings["checks_s"] = round(time.monotonic() - started, 3)
        if not ok:
            code = (
                "token_cannot_write"
                if any(o.outcome.value == "refused" for o in blocking)
                else "publish_refused_amber"
            )
            raise _Stop(
                PublishStatus.REFUSED,
                {
                    "code": code,
                    "message": blocking[0].reason + " " + blocking[0].next_step,
                    "details": {
                        "checks": [o.as_dict() for o in blocking],
                        "repository_public": effective == "public"
                        and pub.requested_visibility == "private",
                    },
                },
            )
        assert hub is not None

        # --- render the manifest and the card ------------------------------------------
        sources = [manifest_source(f) for f in assembled.sources]
        caveats = card_mod.caveats_from_outcomes(outcomes)
        if build.omitted and int(build.omitted["overrides_applied"]):
            caveats.append(
                {
                    "code": "overrides_applied",
                    "severity": "note",
                    "message": f"{int(build.omitted['overrides_applied']):,} review override(s) replaced model labels.",
                    "detail": {"count": int(build.omitted["overrides_applied"])},
                }
            )
        if any_forbids(assembled.sources):
            caveats.append(
                {
                    "code": "evaluation_only",
                    "severity": "amber",
                    "message": card_mod.EVALUATION_ONLY,
                    "detail": {},
                }
            )
        content = rows_content(
            build_files=build.files,
            columns=build.columns or [],
            label_column=build.projection["label_column"],
            omitted=build.omitted
            or {"excluded": 0, "flagged_unresolved": 0, "overrides_applied": 0},
            labelers=assembled.labelers,
            calibration=assembled.calibration,
        )
        document = build_document(
            version=version_identity(version, assembled.dataset.name),
            target=dataset_target(assembled.dataset.target_type),
            sources=sources,
            content=content,
            caveats=caveats,
            publication={"state": "in_repository", "repo_id": pub.repo_id, "repo_type": "dataset"},
            generated_at=build.completed_at,
            extensions={"lineage": assembled.lineage},
        )
        manifest_file = _write(pub.id, MANIFEST_FILE, manifest_bytes(document), FileRole.MANIFEST)
        splits = split_files(build)
        previous_readme: str | None = None
        if state.exists and state.head and any(f.path == CARD_FILE for f in state.files):
            previous_readme = hub.download(pub.repo_id, CARD_FILE, state.head).decode("utf-8")
        history = card_mod.merge_history(
            card_mod.parse_history(previous_readme),
            {
                "version": version.id,
                "commit": card_mod.THIS_COMMIT,
                "date": timestamp(utc_now())[:10],
            },
            recorded_commits(session, pub.repo_id),
        )
        record = card_mod.record_section(
            document,
            outcomes,
            drop_summary=version.drop_summary,
            digests=[
                {"path": f.path, "bytes": f.bytes, "sha256": f.sha256}
                for f in [*splits, manifest_file]
            ],
            history=history,
        )
        prose = pub.card_prose or card_mod.default_prose(document)
        card_bytes = card_mod.assemble(card_mod.front_matter(document), prose, record)
        card_file = _write(pub.id, CARD_FILE, card_bytes, FileRole.CARD)
        built = [*splits, card_file, manifest_file]
        _update(session, pub, card_sha256=card_file.sha256, manifest_sha256=manifest_file.sha256)

        decision = plan(
            state,
            built,
            max_bytes=settings.publish_max_file_bytes,
            prior_dw_paths=prior_dw_paths(session, pub.repo_id, pub.id),
        )
        if isinstance(decision, Refusal):
            raise _Stop(
                PublishStatus.REFUSED,
                {"code": decision.code, "message": decision.message, "details": decision.details},
            )
        if decision.no_change:
            assert state.head is not None
            visibility = "private" if state.private else "public"
            _record_files(session, pub, compare(built, state.files))
            raise _Stop(PublishStatus.NO_CHANGE, None) from _NoChange(state.head, visibility)

        # --- upload ----------------------------------------------------------------------
        if cancel_requested():
            raise _Stop(
                PublishStatus.CANCELLED,
                {"code": "cancelled", "message": "Cancelled before upload."},
            )
        _update(session, pub, status=PublishStatus.UPLOADING)
        progress("uploading", 30.0)
        upload_started = time.monotonic()
        if not state.exists:
            hub.create_private_repo(pub.repo_id)
        commit = _commit_once(hub, pub, decision)
        timings["upload_s"] = round(time.monotonic() - upload_started, 3)
        too_late = cancel_requested()
        _update(session, pub, commit=commit, cancel_too_late=too_late)

        # --- verify ----------------------------------------------------------------------
        _update(session, pub, status=PublishStatus.VERIFYING)
        progress("verifying", 70.0)
        verify_started = time.monotonic()
        result = verify_commit(
            hub, pub.repo_id, commit, built, {f.split or "": f.path for f in splits}
        )
        timings["verify_s"] = round(time.monotonic() - verify_started, 3)
        _record_files(session, pub, result)
        if not result.ok:
            raise _Stop(
                PublishStatus.VERIFICATION_FAILED,
                {
                    "code": "verification_failed",
                    "message": "The Hub's files at the new commit differ from the files built. "
                    "The repository was left private and the version is not published.",
                    "details": result.summary(),
                },
            )

        # --- visibility ------------------------------------------------------------------
        visibility = "private"
        if effective == "public":
            if state.exists and state.private is False:
                visibility = "public"
            else:
                hub.set_public(pub.repo_id)
                if hub.is_private(pub.repo_id):
                    raise _Stop(
                        PublishStatus.FAILED,
                        {
                            "code": "visibility_not_changed",
                            "message": "The Hub still reports the repository private after the "
                            "change was requested. It stays private; try again.",
                        },
                    )
                visibility = "public"
        published = with_publication(
            document,
            {
                "state": "published",
                "repo_id": pub.repo_id,
                "repo_type": "dataset",
                "visibility": visibility,
                "commit": commit,
                "published_at": timestamp(utc_now()),
                "verification": {
                    "result": "hashes_match",
                    "files_checked": len(result.files),
                    "checked_at": timestamp(utc_now()),
                },
            },
        )
        timings["total_s"] = round(time.monotonic() - started, 3)
        _update(
            session,
            pub,
            status=PublishStatus.PUBLISHED,
            visibility_after=visibility,
            published_manifest=published,
            timings=timings,
            completed_at=utc_now(),
        )
        progress("published", 100.0)
        logger.info("publish %s published %s at %s (%s)", pub.id, pub.repo_id, commit, visibility)
        return {"status": "published", "commit": commit, "visibility": visibility}
    except _Stop as stop:
        session.rollback()
        pub = session.get(Publish, publish_id, populate_existing=True)
        assert pub is not None
        values: dict[str, Any] = {
            "status": stop.status,
            "error": stop.error,
            "timings": timings,
            "completed_at": utc_now(),
        }
        if isinstance(stop.__cause__, _NoChange):
            values["commit"] = stop.__cause__.commit
            values["visibility_after"] = stop.__cause__.visibility
        _update(session, pub, **values)
        logger.info("publish %s ended %s", publish_id, stop.status)
        return {"status": stop.status, "error": stop.error}
    except Exception as exc:  # noqa: BLE001 - recorded on the publish, without the Hub's text
        from ...hub.hub_client import HubError

        session.rollback()
        error = (
            exc.as_dict()
            if isinstance(exc, HubError)
            else {
                "code": "publish_failed",
                "message": f"The publish failed ({type(exc).__name__}).",
            }
        )
        logger.exception("publish %s failed", publish_id)
        pub = session.get(Publish, publish_id, populate_existing=True)
        assert pub is not None
        if pub.status not in (
            PublishStatus.PUBLISHED,
            PublishStatus.NO_CHANGE,
            PublishStatus.REFUSED,
            PublishStatus.VERIFICATION_FAILED,
            PublishStatus.FAILED,
            PublishStatus.CANCELLED,
        ):
            _update(
                session,
                pub,
                status=PublishStatus.FAILED,
                error=error,
                timings=timings,
                completed_at=utc_now(),
            )
        return {"status": PublishStatus.FAILED.value, "error": error}


class _NoChange(Exception):
    def __init__(self, commit: str, visibility: str) -> None:
        super().__init__(commit)
        self.commit = commit
        self.visibility = visibility


def _commit_once(hub: Hub, pub: Publish, decision: Plan) -> str:
    """One commit. A transport failure is recovered by the marker; a commit never lands twice."""
    from ...hub.hub_client import HubError

    message = f"miDataworks: publish version {pub.version_id} {marker(pub.id)}"
    try:
        return hub.commit(pub.repo_id, decision, message)
    except HubError as exc:
        if exc.code == "repo_head_moved":
            raise _Stop(PublishStatus.REFUSED, exc.as_dict()) from None
        if exc.code not in ("hub_transport", "hub_unavailable"):
            raise _Stop(PublishStatus.FAILED, exc.as_dict()) from None
        found = hub.find_commit_by_marker(pub.repo_id, marker(pub.id), pub.head_before)
        if found is not None:
            logger.info("publish %s: the commit landed (%s); response was lost", pub.id, found)
            return found
        try:
            return hub.commit(pub.repo_id, decision, message)
        except HubError as again:
            raise _Stop(PublishStatus.FAILED, again.as_dict()) from None


def verify_commit(
    hub: Hub, repo_id: str, commit: str, built: list[BuiltFile], splits: dict[str, str]
) -> VerificationResult:
    result = compare(built, hub.siblings(repo_id, commit))
    if CARD_FILE not in result.missing:
        ok, problem = check_configs(hub.download(repo_id, CARD_FILE, commit), splits)
        result = with_configs(result, ok, problem)
    return result


def _record_files(session: Session, pub: Publish, result: VerificationResult) -> None:
    for f in result.files:
        session.add(
            PublishFile(
                publish_id=pub.id,
                path_in_repo=f.path,
                role=f.role,
                split=f.split,
                bytes=f.bytes,
                sha256=f.sha256,
                git_blob_sha1=f.git_blob_sha1,
                remote_lfs_sha256=f.remote_lfs_sha256,
                remote_blob_id=f.remote_blob_id,
                remote_size=f.remote_size,
                match=f.match,
            )
        )
    session.commit()


def outcomes_of(pub: Publish) -> list[CheckOutcome]:
    return [CheckOutcome.from_dict(o) for o in pub.check_snapshot or []]


# --- the check-run preview and re-verify (FR-008.15, FR-008.16, FR-008.57) ----------------------


def run_check_run(session: Session, check_run_id: str, hub: Hub | None) -> list[dict[str, Any]]:
    """Run C-1..C-7 for the Publish screen and agents, in the worker (C-2 needs the token).

    A preview: its result is stored once and never trusted by a publish, which re-runs the checks.
    """
    from ...models.publish import CheckRunStatus, PublishCheckRun

    run = session.get(PublishCheckRun, check_run_id)
    assert run is not None
    build = session.get(PublishBuild, run.build_id)
    version = session.get(Version, run.version_id)
    assert build is not None and version is not None
    token_scope: TokenScope = "missing" if hub is None else hub.whoami_scope(run.repo_id)
    state = (
        hub.read_state(run.repo_id)
        if hub is not None and token_scope != "invalid"  # noqa: S105
        else RepoState(False, None, None)
    )
    effective = run.requested_visibility
    if state.exists and state.private is False:
        effective = "public"
    assembled = assemble(
        session,
        version,
        label_column=build.projection["label_column"],
        visibility=effective,
        token_scope=token_scope,
    )
    evaluated = evaluate_checks(assembled.inputs)
    outcomes = [o.as_dict() for o in evaluated]
    allowed, _ = push_allowed(evaluated, effective)
    results = [
        *outcomes,
        {
            "check": "repository",
            "outcome": "note",
            "reason": (
                f"{run.repo_id} exists and is {'private' if state.private else 'public'}."
                if state.exists
                else f"{run.repo_id} does not exist yet; it will be created private."
            ),
            "next_step": (
                "Nothing to do."
                if effective == run.requested_visibility
                else ("The repository is public, so public checks apply to this push.")
            ),
            "evidence": {
                "exists": state.exists,
                "private": state.private,
                "effective_visibility": effective,
                "push_allowed": allowed,
            },
        },
    ]
    run.results = results
    run.licence_table_version = TABLE.version
    run.status = CheckRunStatus.COMPLETED
    run.completed_at = utc_now()
    session.commit()
    return results


def run_reverify(session: Session, publish_id: str, hub: Hub) -> dict[str, Any]:
    """Compare the recorded files with the Hub at the recorded commit; report head drift.

    Writes nothing to the Hub and nothing to the (immutable) publish record; the result is the
    job's.
    """
    pub = session.get(Publish, publish_id)
    assert pub is not None
    if pub.commit is None:
        return {"status": "nothing_to_verify", "message": f"Publish {pub.id} recorded no commit."}
    files = list(
        session.execute(select(PublishFile).where(PublishFile.publish_id == pub.id)).scalars()
    )
    built = [
        BuiltFile(
            path=f.path_in_repo,
            local_path="",
            role=f.role,
            split=f.split,
            bytes=f.bytes,
            sha256=f.sha256,
            git_blob_sha1=f.git_blob_sha1,
        )
        for f in files
    ]
    splits = {f.split or "": f.path for f in built if f.role == FileRole.SPLIT}
    result = verify_commit(hub, pub.repo_id, pub.commit, built, splits)
    head = hub.read_state(pub.repo_id).head
    return {
        "status": "hashes_match" if result.ok else "mismatch",
        "commit": pub.commit,
        "head": head,
        "head_moved": head != pub.commit,
        "verification": result.summary(),
        "files": [
            {
                "path": f.path,
                "match": f.match,
                "sha256": f.sha256,
                "remote_lfs_sha256": f.remote_lfs_sha256,
                "remote_blob_id": f.remote_blob_id,
            }
            for f in result.files
        ],
    }
