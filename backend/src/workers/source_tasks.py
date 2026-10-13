"""Feature 001's Celery tasks (FR-001.1, 001.3, 001.4, 001.8, 001.9, 001.21–001.25; 001 FTDD §2).

- ``midataworks.sources.preview_hf`` (``default``): a preview the API waits on; returns facts or an
  error envelope, never a token.
- ``midataworks.sources.import_source`` (``curation``): one task for both import paths, chosen by the
  job's ``mode`` (``hf`` or ``upload``) — the job-kind registry names ONE task per kind, and both paths
  are kind ``source_import`` (recorded as a deviation from the FTID's two task names).

The token: taken from the ephemeral store once (``GETDEL``), else the stored token decrypted here in
the worker, else none. It is passed as an argument to the Hub client and the loader and is never
written to a row, a job, a result, an event, a file or a log.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..core import ephemeral_secrets
from ..core.cancellation import (
    CancelWatchdog,
    OperatorCancelled,
    cooperative_cancel,
    record_progress,
)
from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..core.storage import resolve_under_data_dir, run_dir, source_dir, staging_dir
from ..models.source import Source
from ..models.source_enums import LICENCE_NOT_STATED, LicenceOrigin, SourceKind, SourceState
from ..services.app_setting_service import int_setting_sync
from ..services.job_service import claim_job, dispatch_queued
from ..services.sources import hf_materialise, source_service, upload_service
from ..services.sources.config_choice import check_split, choose_config
from ..services.sources.detection import detect_stored
from ..services.sources.licence import licence_from_hub
from ..services.sources.preview_service import build_preview
from ..services.sources.revision import resolve_revision
from ..services.sources.tokens import choose_token
from . import janitor
from .emit import emit
from .secrets import resolve_hf_token

logger = logging.getLogger(__name__)

ROOM = "dataworks/source-imports/{id}"
#: Tests replace the loader; production uses ``datasets.load_dataset`` (``test_loader_binding.py``).
LOADER: hf_materialise.Loader | None = None


def library_versions() -> dict[str, str]:
    import datasets
    import huggingface_hub
    import pyarrow

    return {
        "datasets": datasets.__version__,
        "huggingface_hub": huggingface_hub.__version__,
        "pyarrow": pyarrow.__version__,
    }


def _token(key: str, supplied: bool) -> tuple[str | None, str]:
    def stored() -> str | None:
        with get_sync_db() as db:
            return resolve_hf_token(db)

    token, tier = choose_token(
        take=lambda: ephemeral_secrets.take(key), stored=stored, supplied=supplied
    )
    return token, tier.value


def _client(token: str | None, tier: str) -> Any:
    from ..clients.hf_hub import HubClient

    return HubClient(token, tier=tier)


def _envelope(error: AppError) -> dict[str, Any]:
    return {"code": error.code, "message": error.message, "details": error.details}


# --------------------------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------------------------


@celery_app.task(name="midataworks.sources.preview_hf")
def preview_hf(preview_id: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        token, tier = _token(preview_id, bool(request.get("token_supplied")))
        return {"preview": build_preview(_client(token, tier), request)}
    except AppError as error:
        return {"error": {**_envelope(error), "status": error.status_code}}


# --------------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------------


def _detect_stored(files: list[dict[str, Any]], directory: Path) -> dict[str, Any] | None:
    return detect_stored([(directory / Path(f["path"]).name, int(f["rows"])) for f in files])


_PHASES: dict[str, tuple[str, float]] = {}


def log_phase(job_id: str, phase: str, source_id: str | None = None, **counts: Any) -> None:
    """One structured line when an import ENTERS a phase (001 FTDD 11, FTASKS 12.7).

    Only ids, the phase name and NUMERIC counts are written: never a request body, a token, a
    file name or row text. ``duration_ms`` is how long the previous phase took."""
    now = time.monotonic()
    previous = _PHASES.get(job_id)
    if previous is not None and previous[0] == phase:
        return
    _PHASES[job_id] = (phase, now)
    numbers = " ".join(
        f"{k}={v}"
        for k, v in sorted(counts.items())
        if isinstance(v, int | float) and v is not True
    )
    logger.info(
        "source_import job_id=%s source_id=%s phase=%s %s duration_ms=%d",
        job_id,
        source_id or "-",
        phase,
        numbers,
        int((now - previous[1]) * 1000) if previous else 0,
    )
    if phase in ("completed", "existing", "failed", "cancelled"):
        _PHASES.pop(job_id, None)


def _progress(job_id: str, phase: str, percent: float, **fields: Any) -> None:
    log_phase(job_id, phase, **fields)
    record_progress(job_id, progress=percent, message=phase)
    emit(
        ROOM.format(id=job_id),
        "source_import:progress",
        {"job_id": job_id, "phase": phase, **fields},
    )


def _finish(job_id: str, source_id: str, existing: bool) -> dict[str, Any]:
    log_phase(job_id, "existing" if existing else "completed", source_id)
    result = {
        "source_id": source_id,
        "existing_source_id": source_id if existing else None,
        "existing": existing,
    }
    record_progress(
        job_id,
        status="completed",
        progress=100.0,
        message="Already imported." if existing else "Imported.",
        result=result,
    )
    emit(
        ROOM.format(id=job_id),
        "source_import:completed",
        {"job_id": job_id, "source_id": source_id, "existing": existing},
    )
    return result


def _import_hf(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    token, tier = _token(job_id, bool(params.get("token_supplied")))
    client = _client(token, tier)
    repo = params["repo_id"]
    _progress(job_id, "resolve", 2.0)
    resolved = resolve_revision(client, repo, params.get("revision"))
    hf_materialise.refuse_remote_code(repo, resolved.siblings)
    from ..clients.hf_hub import ViewerUnavailable

    try:
        config_splits = client.viewer_splits(repo)
    except ViewerUnavailable:
        config_splits = []
    configs = list(dict.fromkeys([cs.config for cs in config_splits] or resolved.card_configs))
    config = choose_config(configs, params.get("config"))
    check_split([cs.split for cs in config_splits if cs.config == config], params.get("split"))
    raw, display, origin = licence_from_hub(resolved.card_license, resolved.tags)
    display_name = repo + (f" ({config})" if config else "")
    with get_sync_db() as db:
        created = source_service.create_importing(
            db,
            fields={
                "kind": SourceKind.HF,
                "display_name": display_name,
                "repo_id": repo,
                "config": config,
                "split_selection": params.get("split") or None,
                "requested_ref": resolved.requested_ref,
                "resolved_commit": resolved.commit,
                "licence_raw": raw,
                "licence_display": display,
                "licence_origin": origin,
                "gated": resolved.gated,
                "token_tier": tier,
                "library_versions": library_versions(),
                "import_job_id": job_id,
                "created_by": params["started_by"],
                "created_by_origin": params["started_by_origin"],
            },
            identity={
                "kind": SourceKind.HF,
                "repo_id": repo,
                "config": config,
                "split_selection": params.get("split") or None,
                "resolved_commit": resolved.commit,
            },
        )
        source_id = created.source.id
    if created.existing:
        return _finish(job_id, source_id, True)
    try:
        expected: int | None = None
        try:
            size = client.viewer_size(repo, config).get("size", {})
            block = size.get("config") or size.get("dataset") or {}
            expected = block.get("num_bytes_parquet_files") or block.get("num_bytes_original_files")
        except ViewerUnavailable:
            expected = None
        if not expected:
            # FTID section 7: else the sum of the file sizes at the pinned commit.
            sizes = client.file_sizes(repo, resolved.commit)
            expected = sum(sizes.values()) or None
        with get_sync_db() as db:
            confirm_bytes = int_setting_sync(db, "import_confirm_bytes")
        hf_materialise.preflight(
            expected, confirm_large=bool(params.get("confirm_large")), confirm_bytes=confirm_bytes
        )
        cache = run_dir(job_id) / "hf_cache"
        staging = staging_dir() / job_id

        def heartbeat() -> None:
            done = (
                sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
                if cache.exists()
                else 0
            )
            _progress(job_id, "download", 10.0, bytes=done, bytes_expected=expected)

        _progress(job_id, "download", 5.0, bytes=0, bytes_expected=expected)
        with CancelWatchdog(job_id, interval_s=2.0, on_tick=heartbeat):
            written = hf_materialise.materialise(
                LOADER or hf_materialise.default_loader(),
                repo_id=repo,
                config=config,
                split=params.get("split") or None,
                commit=resolved.commit,
                cache_dir=cache,
                token=token,
                staging=staging,
                tier=tier,
            )
        return _commit(job_id, source_id, staging, written)
    except BaseException as error:
        _end(source_id, error)
        raise
    finally:
        shutil.rmtree(run_dir(job_id) / "hf_cache", ignore_errors=True)
        shutil.rmtree(staging_dir() / job_id, ignore_errors=True)


def _commit(job_id: str, source_id: str, staging: Path, written: list[Any]) -> dict[str, Any]:
    _progress(job_id, "commit", 95.0, source_id=source_id, files=len(written))
    destination = source_dir(source_id)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(destination)  # the rename BEFORE ready (FR-001.31; mutation control M6)
    files = [
        {
            "split": w.split,
            "path": f"sources/{source_id}/{w.path.name}",
            "rows": w.rows,
            "bytes": w.bytes,
            "sha256": w.sha256,
            "columns": w.columns,
        }
        for w in written
    ]
    detection = _detect_stored(files, destination)
    with get_sync_db() as db:
        source = db.get(Source, source_id)
        assert source is not None
        source_service.commit_ready(db, source, files, detection)
    logger.info(
        "source %s ready: %d files, %d rows", source_id, len(files), sum(f["rows"] for f in files)
    )
    return _finish(job_id, source_id, False)


def _end(source_id: str, error: BaseException) -> None:
    """Mark an importing source cancelled or failed, and remove anything it had renamed in."""
    if isinstance(error, OperatorCancelled):
        state = SourceState.CANCELLED
        envelope = {"code": "cancelled", "message": "Cancelled by the operator.", "details": {}}
    elif isinstance(error, AppError):
        state, envelope = SourceState.FAILED, _envelope(error)
    else:
        state = SourceState.FAILED
        envelope = {"code": "import_failed", "message": "The import failed.", "details": {}}
    try:
        with get_sync_db() as db:
            if source_service.end_import(db, source_id, state, envelope):
                shutil.rmtree(source_dir(source_id), ignore_errors=True)
    except Exception as exc:  # noqa: BLE001 - the job's own failure is what the operator sees
        logger.warning("could not mark source %s %s: %s", source_id, state.value, exc)


def reap_source_import(db: Any, job_id: str, status: str, reason: str) -> None:
    """Janitor hook (001 FTASKS 7.9): a reaped import's source is failed and its staging swept."""
    row = db.execute(
        select(Source).where(Source.import_job_id == job_id, Source.state == SourceState.IMPORTING)
    ).scalar_one_or_none()
    if row is not None:
        state = SourceState.CANCELLED if status == "cancelled" else SourceState.FAILED
        source_service.end_import(
            db, row.id, state, {"code": "worker_lost", "message": reason, "details": {}}
        )
        shutil.rmtree(source_dir(row.id), ignore_errors=True)
    shutil.rmtree(run_dir(job_id) / "hf_cache", ignore_errors=True)
    shutil.rmtree(staging_dir() / job_id, ignore_errors=True)


janitor.register_on_reap("source_import", reap_source_import)


def _import_upload(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    staged_upload = resolve_under_data_dir("staging", params["upload_dir"])
    with get_sync_db() as db:
        created = source_service.create_importing(
            db,
            fields={
                "kind": SourceKind.UPLOAD,
                "display_name": params["display_name"],
                "content_hash": params["content_hash"],
                "parse_options": params.get("csv") or None,
                "licence_raw": None,
                "licence_display": LICENCE_NOT_STATED,
                "licence_origin": LicenceOrigin.NONE.value,
                "library_versions": library_versions(),
                "import_job_id": job_id,
                "created_by": params["started_by"],
                "created_by_origin": params["started_by_origin"],
            },
            identity={"kind": SourceKind.UPLOAD, "content_hash": params["content_hash"]},
        )
        source_id = created.source.id
    if created.existing:
        shutil.rmtree(staged_upload, ignore_errors=True)
        return _finish(job_id, source_id, True)
    staging = staging_dir() / job_id
    try:
        written: list[Any] = []
        originals = staging / "original"
        originals.mkdir(parents=True, exist_ok=True)
        for position, entry in enumerate(params["files"]):
            received = upload_service.Received(
                staged_upload / entry["staged"], entry["bytes"], entry["sha256"], entry["name"]
            )
            out = staging / hf_materialise.file_name(entry["split"], position)
            converted = upload_service.validate_and_convert(
                received, entry["split"], params.get("csv"), out
            )
            (staged_upload / entry["staged"]).rename(originals / entry["staged"])
            written.append(converted)
            _progress(
                job_id,
                "convert",
                10.0 + 80.0 * (position + 1) / len(params["files"]),
                split=entry["split"],
                rows=converted["rows"],
            )
        destination = source_dir(source_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(destination)  # the rename BEFORE ready (FR-001.31)
        files = [
            {
                "split": w["split"],
                "path": f"sources/{source_id}/{hf_materialise.file_name(w['split'], i)}",
                "rows": w["rows"],
                "bytes": w["bytes"],
                "sha256": w["sha256"],
                "columns": w["columns"],
                "original_name": w["original_name"],
                "original_sha256": w["original_sha256"],
                "original_bytes": w["original_bytes"],
            }
            for i, w in enumerate(written)
        ]
        detection = _detect_stored(files, destination)
        with get_sync_db() as db:
            source = db.get(Source, source_id)
            assert source is not None
            source_service.commit_ready(db, source, files, detection)
        return _finish(job_id, source_id, False)
    except BaseException as error:
        _end(source_id, error)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(staged_upload, ignore_errors=True)


def _failed(job_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
    log_phase(job_id, "failed")
    record_progress(job_id, status="failed", error=envelope["message"], result={"error": envelope})
    emit(ROOM.format(id=job_id), "source_import:failed", {"job_id": job_id, "error": envelope})
    return {"status": "failed", "job_id": job_id, "code": envelope["code"]}


@cooperative_cancel
def run_import(job_id: str) -> dict[str, Any]:
    with get_sync_db() as db:
        job = claim_job(db, job_id)
        if job is None:
            return {"status": "skipped", "job_id": job_id}
        params = {
            **job.params,
            "started_by": job.started_by,
            "started_by_origin": job.started_by_origin,
        }
    started = time.monotonic()
    try:
        result = (
            _import_upload(job_id, params)
            if params["mode"] == "upload"
            else _import_hf(job_id, params)
        )
    except AppError as error:
        logger.info(
            "import job %s failed: %s (%d ms)",
            job_id,
            error.code,
            int((time.monotonic() - started) * 1000),
        )
        return _failed(job_id, _envelope(error))
    except OperatorCancelled:
        log_phase(job_id, "cancelled")
        emit(ROOM.format(id=job_id), "source_import:cancelled", {"job_id": job_id})
        raise
    except Exception:
        # The library's message is not shown (it can carry paths and URLs); the log has it, and
        # every token in this process is registered with the redactor.
        logger.exception("import job %s failed unexpectedly", job_id)
        return _failed(
            job_id,
            {
                "code": "import_failed",
                "message": "The import failed unexpectedly. The worker log has the detail; "
                "start it again, and report it if it repeats.",
                "details": {},
            },
        )
    logger.info(
        "import job %s finished: source %s (%d ms)",
        job_id,
        result.get("source_id"),
        int((time.monotonic() - started) * 1000),
    )
    return result


@celery_app.task(name="midataworks.sources.import_source", acks_late=True)
def import_source(job_id: str) -> dict[str, Any]:
    try:
        return run_import(job_id)
    finally:
        try:
            with get_sync_db() as db:
                dispatch_queued(db)
        except Exception as exc:  # noqa: BLE001 - Beat dispatches again
            logger.warning("Could not dispatch the next queued job: %s", exc)
