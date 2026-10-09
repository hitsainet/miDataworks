"""The job-kind registry (ADR-007; Foundation tasks 5.2, 5.2a).

Every job kind declares the three things its lifecycle needs: a cancel scope, a janitor heartbeat
limit and a Socket.IO room. ``tests/unit/test_job_kind_registry.py`` reads this live registry and
fails if a kind lacks any of them, and :func:`register_job_kind` refuses a declaration that is
incomplete, so a kind cannot be half-registered.

Feature hand-off: a feature adds its kind here (or calls :func:`register_job_kind` from a module
the app imports at start) — never a private table of its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from .cancellation import SCOPES

_ROOM = re.compile(r"^dataworks/[a-z0-9][a-z0-9-]*/\{id\}$")


@dataclass(frozen=True)
class JobKind:
    name: str
    cancel_scope: str
    janitor_heartbeat_limit: timedelta
    room_template: str
    #: The Celery task that runs it, or None when a feature has not built it yet.
    task_name: str | None = None
    description: str = ""

    def room(self, job_id: str) -> str:
        return self.room_template.replace("{id}", job_id)


JOB_KINDS: dict[str, JobKind] = {}


class JobKindError(ValueError):
    """A job kind declaration is incomplete or conflicts with another."""


def register_job_kind(kind: JobKind) -> JobKind:
    """Add a kind to the live registry, refusing an incomplete or duplicate declaration."""
    if not kind.name or not re.fullmatch(r"[a-z][a-z0-9_]*", kind.name):
        raise JobKindError(f"job kind name {kind.name!r} must be snake_case")
    if kind.cancel_scope not in SCOPES:
        raise JobKindError(f"{kind.name}: cancel scope {kind.cancel_scope!r} is not registered")
    if kind.janitor_heartbeat_limit <= timedelta(0):
        raise JobKindError(f"{kind.name}: janitor heartbeat limit must be positive")
    if not _ROOM.fullmatch(kind.room_template):
        raise JobKindError(
            f"{kind.name}: room {kind.room_template!r} must look like dataworks/<kind>/{{id}}"
        )
    if kind.task_name is not None and not kind.task_name.startswith("midataworks."):
        raise JobKindError(f"{kind.name}: task {kind.task_name!r} must start with midataworks.")
    existing = JOB_KINDS.get(kind.name)
    if existing is not None and existing != kind:
        raise JobKindError(f"job kind {kind.name!r} is already registered differently")
    JOB_KINDS[kind.name] = kind
    return kind


def get_job_kind(name: str) -> JobKind:
    try:
        return JOB_KINDS[name]
    except KeyError:
        raise JobKindError(f"unknown job kind {name!r}") from None


# --- Foundation's kinds ---------------------------------------------------------------------

SELFTEST = register_job_kind(
    JobKind(
        name="selftest",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=5),
        room_template="dataworks/selftest/{id}",
        task_name="midataworks.selftest.run",
        description="Walking skeleton: writes Parquet, reads it with DuckDB, reports, cancels.",
    )
)

#: Feature 005's label runs (Stage 3, requested by 005). The room's ``{id}`` is the LABEL RUN's id,
#: not a job's: a run has several jobs across resumes and the screen follows the run (FR-005.30).
LABEL_RUN = register_job_kind(
    JobKind(
        name="label_run",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/label-runs/{id}",
        task_name="midataworks.labeling.run_label_run",
        description="Classifier or judge labeling run (feature 005).",
    )
)

#: Feature 005: "Estimate keep share" — scores a random sample, writes no labels (FR-005.19).
LABEL_PREVIEW = register_job_kind(
    JobKind(
        name="label_preview",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/label-previews/{id}",
        task_name="midataworks.labeling.run_label_preview",
        description="Keep-share estimate on a sample; results in the job record (feature 005).",
    )
)

#: Feature 005: new thresholds over a parent run's stored probabilities, no endpoint call
#: (FR-005.21). Same room as the run it writes.
LABEL_REDERIVE = register_job_kind(
    JobKind(
        name="label_rederive",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/label-runs/{id}",
        task_name="midataworks.labeling.run_rederive",
        description="Re-derive labels with new thresholds (feature 005).",
    )
)

#: Feature 005: combine completed judge runs (FR-005.45).
LABEL_AGGREGATE = register_job_kind(
    JobKind(
        name="label_aggregate",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/label-runs/{id}",
        task_name="midataworks.labeling.run_aggregate",
        description="Aggregate several judge runs over the same rows (feature 005).",
    )
)

#: The shared miLLM model-lease holder: renewal and crash cleanup (Stage 3, requested by 005).
LEASE = register_job_kind(
    JobKind(
        name="lease",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=10),
        room_template="dataworks/leases/{id}",
        description="Holds a miLLM model lease for the jobs that need that model (ADR-012).",
    )
)


#: Feature 006: compute one calibration record (FR-006.21). Reads labels; never calls a model.
CALIBRATION_COMPUTE = register_job_kind(
    JobKind(
        name="calibration_compute",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=10),
        room_template="dataworks/calibration/{id}",
        task_name="midataworks.calibration.compute_record",
        description="Compute a labeler's calibration record against human labels (feature 006).",
    )
)

# --- feature 002 -----------------------------------------------------------------------------


def _version_janitor_limit() -> timedelta:
    from .config import get_settings

    return timedelta(minutes=get_settings().version_build_janitor_minutes)


#: A recipe build (FR-002.36). Liveness also counts the heartbeat of the step it waits on.
VERSION_BUILD = register_job_kind(
    JobKind(
        name="version_build",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=_version_janitor_limit(),
        room_template="dataworks/version-builds/{id}",
        task_name="midataworks.versions.advance_build",
        description="Build a version from inputs, a recipe revision and a seed (feature 002).",
    )
)

#: A verify rebuild (FR-002.6): the same build with reuse disabled, compared by logical digest.
VERSION_VERIFY = register_job_kind(
    JobKind(
        name="version_verify",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=_version_janitor_limit(),
        room_template="dataworks/version-verifications/{id}",
        task_name="midataworks.versions.verify_rebuild",
        description="Rebuild a version with step reuse off and compare its digests (feature 002).",
    )
)


# --- feature 001 -----------------------------------------------------------------------------


def _source_janitor_limit() -> timedelta:
    from .config import get_settings

    return timedelta(seconds=get_settings().source_import_janitor_limit_s)


#: An HF import or an upload (001 FTDD section 2). The download heartbeats through CancelWatchdog's
#: on_tick (cache bytes), so a long download is never mistaken for a dead one.
SOURCE_IMPORT = register_job_kind(
    JobKind(
        name="source_import",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=_source_janitor_limit(),
        room_template="dataworks/source-imports/{id}",
        task_name="midataworks.sources.import_source",
        description="Import a Hugging Face dataset at a pinned commit, or an upload (feature 001).",
    )
)


# --- feature 008 -----------------------------------------------------------------------------


def _publish_janitor_limit() -> timedelta:
    from .config import get_settings

    return timedelta(seconds=get_settings().publish_janitor_limit_seconds)


#: (kind, room segment, task, description). All five run on the `publish` queue (FR-008.54).
_PUBLISH_KINDS: tuple[tuple[str, str, str, str], ...] = (
    (
        "publish_build",
        "publish-builds",
        "midataworks.publish.build",
        "Write one Parquet file per split for a publish or an export (feature 008).",
    ),
    (
        "publish_check",
        "publish-checks",
        "midataworks.publish.check",
        "Run checks C-1 to C-7 for a build, repository and visibility (feature 008).",
    ),
    (
        "publish",
        "publishes",
        "midataworks.publish.publish",
        "Push a build to the Hugging Face Hub, verify every hash, record the commit (008).",
    ),
    (
        "publish_reverify",
        "publish-reverifications",
        "midataworks.publish.reverify",
        "Compare a recorded publish with the Hub at its commit; report head drift (008).",
    ),
    (
        "export",
        "exports",
        "midataworks.publish.export",
        "Write TRL-ready files or a miForge set with the handoff manifest (feature 008).",
    ),
)

PUBLISH_KINDS: dict[str, JobKind] = {
    name: register_job_kind(
        JobKind(
            name=name,
            cancel_scope="dw_jobs",
            janitor_heartbeat_limit=_publish_janitor_limit(),
            room_template=f"dataworks/{room}/{{id}}",
            task_name=task,
            description=description,
        )
    )
    for name, room, task, description in _PUBLISH_KINDS
}


# --- feature 004 -----------------------------------------------------------------------------

#: A report too large to compute inline (FTDD 004 section 5.2): profile, audit, leakage,
#: contamination or clusters, completing the ``running`` row its params name.
CURATION_REPORT = register_job_kind(
    JobKind(
        name="curation_report",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/curation-reports/{id}",
        task_name="midataworks.curation.run_report",
        description="Compute a curation report for a version (feature 004).",
    )
)


# --- feature 009 -----------------------------------------------------------------------------


def _detector_send_janitor_limit() -> timedelta:
    from .config import get_settings

    return timedelta(seconds=get_settings().detector_send_janitor_limit_seconds)


#: A detector-set send (009 FR-009.17). The room's ``{id}`` is the SEND's id, not a job's: a send
#: has several jobs across resumes and the screen follows the send. Heartbeats every download poll.
MISTUDIO_SEND = register_job_kind(
    JobKind(
        name="mistudio_send",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=_detector_send_janitor_limit(),
        room_template="dataworks/detector-sends/{id}",
        task_name="midataworks.detector_sets.run_mistudio_send",
        description="Publish, download into miStudio and register a detector set's roles (009).",
    )
)


# --- feature 007 -----------------------------------------------------------------------------

#: A generation run (FTDD 007 section 6.3). The room's ``{id}`` is the RUN's id: a run has several
#: jobs across resumes and the screen follows the run (as 005's label runs do).
GENERATION_RUN = register_job_kind(
    JobKind(
        name="generation_run",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/generation-runs/{id}",
        task_name="midataworks.generation.run",
        description="Generate rows (and steered pairs) through an endpoint; records only (007).",
    )
)

#: A diversity report over one version and its reference (FTDD 007 section 6.6).
DIVERSITY_REPORT = register_job_kind(
    JobKind(
        name="diversity_report",
        cancel_scope="dw_jobs",
        janitor_heartbeat_limit=timedelta(minutes=15),
        room_template="dataworks/diversity-reports/{id}",
        task_name="midataworks.generation.diversity_report",
        description="Distinct-n, embedding spread and cluster coverage against a reference (007).",
    )
)
