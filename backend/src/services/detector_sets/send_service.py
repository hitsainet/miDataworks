"""Starting, approving, resuming and cancelling a detector-set send (FR-009.16 - FR-009.30,
FR-009.73, FR-009.75, FR-009.78, FR-009.82; FTDD 009 sections 2.2, 5.2; FTID 009 section 5).

``start`` runs, in order: the checks (``checks.evaluate`` / ``send_allowed`` — nothing else decides),
the repositories, 008's builds and request digests, the plan and its approval digest. Then:

- an OPERATOR start inserts the send, its step rows and its ``dw_jobs`` row in ONE transaction
  before dispatch (miStudio's lesson: committed before dispatch, and the task refuses without a row);
- an AGENT start never reaches this module's insert: the REST gate stores the request with
  :func:`approval_facts` (the send ID it will have and the approval digest) and answers ``202``; the
  operator's approval replays the route once, and :func:`start` recomputes the plan and refuses
  ``approval_mismatch`` when the digest differs (FR-009.82). 008 independently refuses a publish
  whose digest the approval does not list (``SendApproval``).

Deviation (controls review): no send row exists in ``awaiting_approval``; the pending approval is
the record until the operator decides, so a rejected approval leaves no send behind.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.canonical_json import canonical_sha256
from ...core.clock import utc_now
from ...core.config import get_settings
from ...core.ids import new_id
from ...models.approval import Approval
from ...models.dataset import Dataset
from ...models.detector_send import (
    ACTIVE_SEND_STATES,
    DetectorSend,
    DetectorSendStep,
)
from ...models.detector_set import DetectorSet, DetectorSetRole
from ...models.job import Job
from ...models.publish import Publish, PublishStatus
from ...models.version import Version
from ..app_setting_service import SETTINGS
from ..publishing import build_service, publish_service
from . import checks, integration, plan, set_service
from .errors import DetectorSetError
from .role_mapping import mistudio_role
from .set_service import Who

logger = logging.getLogger(__name__)

JOB_KIND = "mistudio_send"


# --- repositories (FR-009.78) -----------------------------------------------------------------


def namespace_setting(session: Session) -> str | None:
    from ...models.app_setting import AppSetting

    assert "detector_hub_namespace" in SETTINGS
    row = session.get(AppSetting, "detector_hub_namespace")
    value = (row.value if row is not None else "") or get_settings().publish_default_namespace
    return value.strip() or None


def repositories(
    session: Session,
    roles: Sequence[DetectorSetRole],
    explicit: Mapping[str, str],
    namespace: str | None,
) -> dict[str, str]:
    """One repository per version: ``<namespace>/<dataset>-v<n>`` unless named (T-46)."""
    out: dict[str, str] = {}
    ns = namespace or namespace_setting(session)
    for version_id in sorted({r.version_id for r in roles}):
        if version_id in explicit:
            out[version_id] = explicit[version_id]
            continue
        if not ns:
            raise DetectorSetError(
                "namespace_required",
                "Name the Hugging Face namespace to publish to, or set detector_hub_namespace in "
                "Settings (FR-009.78).",
                {"version_id": version_id},
            )
        version = session.get(Version, version_id)
        assert version is not None
        dataset = session.get(Dataset, version.dataset_id)
        assert dataset is not None
        out[version_id] = f"{ns}/{dataset.name}-v{version.number}"
    return out


# --- the plan ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Prepared:
    set_row: DetectorSet
    snapshot: dict[str, Any]
    snapshot_sha256: str
    outcomes: list[checks.CheckOutcome]
    plan: dict[str, Any]
    digest: str
    repositories: dict[str, str]


def _reusable_publish(session: Session, version_id: str, repo_id: str) -> Publish | None:
    """FR-009.19: a verified publish of this version to this repository, not since replaced."""
    pub = publish_service.latest_published(session, version_id)
    if pub is None or pub.repo_id != repo_id or pub.commit is None:
        return None
    newest = session.execute(
        select(Publish)
        .where(Publish.repo_id == repo_id, Publish.status == PublishStatus.PUBLISHED)
        .order_by(Publish.completed_at.desc(), Publish.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return pub if newest is not None and newest.id == pub.id else None


def _publish_units(
    session: Session,
    roles: Sequence[DetectorSetRole],
    repos: Mapping[str, str],
    visibility: str,
    who: Who,
) -> list[plan.PublishUnit]:
    """008's request and digest per distinct version; builds are started when missing."""
    units: list[plan.PublishUnit] = []
    pending: list[dict[str, Any]] = []
    for version_id in sorted(repos):
        repo = repos[version_id]
        label_column = next(r.label_column for r in roles if r.version_id == version_id)
        reuse = _reusable_publish(session, version_id, repo)
        if reuse is not None:
            units.append(
                plan.PublishUnit(
                    version_id, repo, label_column, visibility, None, "", None, reuse.id
                )
            )
            continue
        outcome = build_service.request_build(
            session, version_id, label_column, started_by=who.who, origin=who.origin
        )
        build = outcome.build
        if build.status != "completed":
            pending.append({"version_id": version_id, "build_id": build.id, "status": build.status})
            continue
        req = publish_service.PublishRequest(
            version_id=version_id,
            build_id=build.id,
            repo_id=repo,
            visibility=visibility,
            card_prose="",
        )
        digest = publish_service.request_digest(session, req)
        units.append(
            plan.PublishUnit(version_id, repo, label_column, visibility, build.id, "", digest)
        )
    if pending:
        from ..job_service import dispatch_queued

        dispatch_queued(session)
        raise DetectorSetError(
            "builds_pending",
            "The files for "
            + ", ".join(p["version_id"] for p in pending)
            + " are being written for publishing (008 builds). Send again when they finish.",
            {"builds": pending},
        )
    return units


def snapshot_of(
    session: Session,
    set_row: DetectorSet,
    roles: Sequence[DetectorSetRole],
    repos: Mapping[str, str],
    visibility: str,
) -> dict[str, Any]:
    return {
        "set": {
            "id": set_row.id,
            "name": set_row.name,
            "positive_meaning": set_row.positive_meaning,
            "monitored_ref": set_row.monitored_ref,
        },
        "roles": [set_service.role_out(set_row, r, roles, session) for r in roles],
        "repositories": dict(sorted(repos.items())),
        "visibility": visibility,
    }


def prepare(
    session: Session,
    set_id: str,
    *,
    explicit_repos: Mapping[str, str],
    namespace: str | None,
    visibility: str,
    who: Who,
) -> Prepared:
    set_row = set_service.get_set(session, set_id)
    base_url = integration.require_mistudio("Sending a detector set")
    gathered = set_service.gather_check_inputs(session, set_row)
    outcomes = checks.evaluate(gathered.inputs)
    if not checks.send_allowed(outcomes):
        first = checks.first_refusal(outcomes)
        assert first is not None
        raise DetectorSetError(
            "send_refused",
            f"{first.code} {first.title}: {first.reason} {first.next_step or ''}".strip(),
            {"checks": [o.as_dict() for o in outcomes]},
        )
    roles = set_service.roles_of(session, set_id)
    busy = session.execute(
        select(DetectorSend.id).where(
            DetectorSend.set_id == set_id, DetectorSend.state.in_(ACTIVE_SEND_STATES)
        )
    ).first()
    if busy is not None:
        raise DetectorSetError(
            "send_in_progress",
            f"Send {busy[0]} of this set is still running; wait for it or cancel it.",
            {"send_id": busy[0]},
        )
    repos = repositories(session, roles, explicit_repos, namespace)
    for repo in sorted(set(repos.values())):
        running = publish_service.active_publish(session, repo)
        if running is not None:
            raise DetectorSetError(
                "send_in_progress",
                f"{running.id} is publishing to {repo}; wait for it to finish (FR-009.30).",
                {"publish_id": running.id, "repo_id": repo},
            )
    units = _publish_units(session, roles, repos, visibility, who)
    role_inputs = [
        plan.RoleInput(
            role_id=r.id,
            role=r.role,
            version_id=r.version_id,
            split=r.split,
            input_column=r.input_column,
            label_column=r.label_column,
            label_mapping=r.label_mapping,
            pair_column=r.pair_column,
            view_name=set_service.role_name(set_row, r, roles),
            expected_counts=gathered.expected_counts[r.id],
        )
        for r in roles
    ]
    built = plan.build_plan(
        role_inputs, units, mistudio_base_url=base_url, manifest_served=False
    ).as_dict()
    snapshot = snapshot_of(session, set_row, roles, repos, visibility)
    snapshot_sha = canonical_sha256(snapshot)
    digest = plan.approval_digest(built, set_id=set_id, snapshot_sha256=snapshot_sha)
    return Prepared(set_row, snapshot, snapshot_sha, outcomes, built, digest, repos)


def approval_facts(
    session: Session,
    set_id: str,
    *,
    explicit_repos: Mapping[str, str],
    namespace: str | None,
    visibility: str,
    who: Who,
) -> dict[str, Any]:
    """What the stored ``hub_push`` approval binds to and shows (008 reads ``send_id`` and
    ``publish_digests``; the replay reads ``approval_digest``)."""
    prepared = prepare(
        session,
        set_id,
        explicit_repos=explicit_repos,
        namespace=namespace,
        visibility=visibility,
        who=who,
    )
    return {
        "send_id": new_id("dsn"),
        "approval_digest": prepared.digest,
        "publish_digests": sorted(
            str(u["digest"]) for u in prepared.plan["publish_units"] if u["digest"]
        ),
        "set_name": prepared.set_row.name,
        "repositories": prepared.repositories,
        "registrations": len(prepared.plan["roles"]),
        "visibility": visibility,
    }


def _steps(send_id: str, built: Mapping[str, Any]) -> list[DetectorSendStep]:
    rows: list[DetectorSendStep] = []
    position = 0
    for u in built["publish_units"]:
        rows.append(
            DetectorSendStep(
                send_id=send_id,
                step="publish",
                unit_key=u["version_id"],
                position=position,
                role_ids=[
                    r["role_id"] for r in built["roles"] if r["version_id"] == u["version_id"]
                ],
                state="pending",
                repo_id=u["repo_id"],
            )
        )
        position += 1
    for d in built["download_units"]:
        rows.append(
            DetectorSendStep(
                send_id=send_id,
                step="download",
                unit_key=d["key"],
                position=position,
                role_ids=list(d["role_ids"]),
                state="pending",
                repo_id=d["repo_id"],
            )
        )
        position += 1
    for r in built["roles"]:
        rows.append(
            DetectorSendStep(
                send_id=send_id,
                step="register",
                unit_key=r["role_id"],
                position=position,
                role_ids=[r["role_id"]],
                state="pending",
                expected_counts=r["expected_counts"],
            )
        )
        position += 1
    return rows


def start(
    session: Session,
    set_id: str,
    *,
    explicit_repos: Mapping[str, str],
    namespace: str | None,
    visibility: str,
    who: Who,
    approval_id: str | None = None,
) -> DetectorSend:
    """Create the send, its steps and its job before dispatch. Agent sends arrive here only as
    the approved replay, with ``approval_id``."""
    prepared = prepare(
        session,
        set_id,
        explicit_repos=explicit_repos,
        namespace=namespace,
        visibility=visibility,
        who=who,
    )
    send_id = new_id("dsn")
    approved_by = None
    if approval_id is not None:
        approval = session.get(Approval, approval_id)
        if approval is None or approval.action != "hub_push":
            raise DetectorSetError("approval_mismatch", "No hub_push approval covers this send.")
        if approval.payload.get("approval_digest") != prepared.digest:
            raise DetectorSetError(
                "approval_mismatch",
                "The set changed after approval; send again for a new approval.",
                {"approved": approval.payload.get("approval_digest"), "now": prepared.digest},
            )
        send_id = str(approval.payload["send_id"])
        approved_by = approval.decided_by
    elif who.origin == "agent":
        raise DetectorSetError(
            "approval_mismatch", "An agent's send needs the operator's approval (P-06)."
        )
    job = Job(
        id=new_id("job"),
        kind=JOB_KIND,
        status="queued",
        progress=0.0,
        params={"send_id": send_id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    session.add(job)
    session.flush()
    send = DetectorSend(
        id=send_id,
        set_id=set_id,
        job_id=job.id,
        job_ids=[job.id],
        snapshot=prepared.snapshot,
        snapshot_sha256=prepared.snapshot_sha256,
        checks=[o.as_dict() for o in prepared.outcomes],
        notes=[o.as_dict() for o in prepared.outcomes if o.outcome == "note"],
        plan=prepared.plan,
        visibility=visibility,
        mistudio_base_url=prepared.plan["mistudio_base_url"],
        approval_digest=prepared.digest,
        state="queued",
        approval_id=approval_id,
        started_by=who.who,
        started_by_origin=who.origin,
        approved_by=approved_by,
    )
    session.add(send)
    session.flush()
    session.add_all(_steps(send_id, prepared.plan))
    session.commit()
    logger.info(
        "detector_send.created send=%s set=%s job=%s origin=%s", send_id, set_id, job.id, who.origin
    )
    _dispatch(session)
    return send


def _dispatch(session: Session) -> None:
    from ..job_service import dispatch_queued

    try:
        dispatch_queued(session)
    except Exception as exc:  # noqa: BLE001 - Beat dispatches again
        logger.warning("Could not dispatch the send job: %s", exc)


# --- reads, resume, cancel --------------------------------------------------------------------


def get_send(session: Session, send_id: str) -> DetectorSend:
    row = session.get(DetectorSend, send_id, populate_existing=True)
    if row is None:
        raise DetectorSetError("send_not_found", f"No detector-set send {send_id}.")
    return row


def sends_of(session: Session, set_id: str) -> list[DetectorSend]:
    return list(
        session.execute(
            select(DetectorSend)
            .where(DetectorSend.set_id == set_id)
            .order_by(DetectorSend.created_at.desc())
        ).scalars()
    )


def steps_of(session: Session, send_id: str) -> list[DetectorSendStep]:
    return list(
        session.execute(
            select(DetectorSendStep)
            .where(DetectorSendStep.send_id == send_id)
            .order_by(DetectorSendStep.position)
            .execution_options(populate_existing=True)
        ).scalars()
    )


def run_request(send: DetectorSend, steps: Sequence[DetectorSendStep]) -> dict[str, Any] | None:
    """FR-009.29: the IDs in miStudio's ``ProbeRunCreate`` shape; in-distribution test first, then
    out-of-distribution in position order. None until every registration is recorded."""
    by_role = {s.unit_key: s.probe_dataset_id for s in steps if s.step == "register"}
    roles = send.snapshot["roles"]
    if not by_role or any(v is None for v in by_role.values()):
        return None

    def ids(kind: str) -> list[str]:
        chosen = sorted((r for r in roles if r["role"] == kind), key=lambda r: r["position"])
        return [str(by_role[r["id"]]) for r in chosen]

    return {
        "train_dataset_id": ids("train")[0],
        "eval_dataset_ids": ids("id_test") + ids("ood_eval"),
        "calibration_dataset_id": ids("calibration_negatives")[0],
    }


def step_out(step: DetectorSendStep) -> dict[str, Any]:
    return {
        "step": step.step,
        "unit_key": step.unit_key,
        "position": step.position,
        "role_ids": step.role_ids,
        "state": step.state,
        "publish_id": step.publish_id,
        "repo_id": step.repo_id,
        "commit": step.commit,
        "mistudio_dataset_id": step.mistudio_dataset_id,
        "probe_dataset_id": step.probe_dataset_id,
        "expected_counts": step.expected_counts,
        "registered_counts": step.registered_counts,
        "request_body": step.request_body,
        "error": step.error,
        "started_at": step.started_at.isoformat() if step.started_at else None,
        "finished_at": step.finished_at.isoformat() if step.finished_at else None,
    }


def send_summary(send: DetectorSend) -> dict[str, Any]:
    return {
        "id": send.id,
        "set_id": send.set_id,
        "state": send.state,
        "job_id": send.job_id,
        "visibility": send.visibility,
        "mistudio_base_url": send.mistudio_base_url,
        "started_by": send.started_by,
        "started_by_origin": send.started_by_origin,
        "approval_id": send.approval_id,
        "created_at": send.created_at.isoformat() if send.created_at else None,
        "completed_at": send.completed_at.isoformat() if send.completed_at else None,
        "error": send.error,
    }


def send_out(session: Session, send: DetectorSend) -> dict[str, Any]:
    steps = steps_of(session, send.id)
    return {
        **send_summary(send),
        "job_ids": send.job_ids,
        "snapshot": send.snapshot,
        "snapshot_sha256": send.snapshot_sha256,
        "checks": send.checks,
        "notes": send.notes,
        "approval_digest": send.approval_digest,
        "approved_by": send.approved_by,
        "steps": [step_out(s) for s in steps],
        "run_request": run_request(send, steps),
        "mistudio_roles": {
            r["id"]: dict(zip(("role", "distribution"), mistudio_role(r["role"]), strict=True))
            for r in send.snapshot["roles"]
        },
    }


def resume(session: Session, send_id: str, who: Who) -> DetectorSend:
    """A new job for the same send; ``done`` and ``reused`` steps are never repeated (FR-009.27)."""
    send = get_send(session, send_id)
    if send.state not in ("failed", "cancelled"):
        raise DetectorSetError(
            "send_terminal" if send.state == "completed" else "send_in_progress",
            f"Send {send_id} is {send.state}; only a failed or cancelled send resumes.",
            {"state": send.state},
        )
    job = Job(
        id=new_id("job"),
        kind=JOB_KIND,
        status="queued",
        progress=0.0,
        params={"send_id": send_id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    session.add(job)
    session.flush()
    for step in steps_of(session, send_id):
        if step.state in ("failed", "running"):
            step.state = "pending"
            step.error = None
    send.job_id = job.id
    send.job_ids = [*send.job_ids, job.id]
    send.state = "queued"
    send.error = None
    send.completed_at = None
    session.commit()
    _dispatch(session)
    return send


def mark(session: Session, send_id: str, state: str, error: dict[str, Any] | None = None) -> None:
    send = get_send(session, send_id)
    send.state = state
    if error is not None:
        send.error = error
    if state in ("completed", "failed", "cancelled"):
        send.completed_at = utc_now()
    session.commit()
