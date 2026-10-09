"""The single entry point for a Hub publish (FR-008.1, 008.2, 008.50–008.53, 008.56, 008.66,
008.67; FTDD 008 section 5.5; FTID 008 section 5).

``request_publish(session, request, authorization, who)`` is called by 008's routes and by 009's
send worker, and by nothing else. It recomputes the request digest and checks the authorization
BEFORE creating anything, so no caller — 009's worker included — can reach the Hub with an
agent-originated request that no approval covers:

- :class:`OperatorOrigin` — an operator request;
- :class:`PublishApproval` — an agent request the operator approved (``hub_push``) whose stored
  payload names exactly this request's digest; executed once;
- :class:`SendApproval` — 009's one approval per send (P-06), whose payload lists this digest.

Anything else raises ``approval_mismatch`` (403). Each approved digest is consumed once.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from ...core.ids import new_id
from ...models.approval import Approval
from ...models.job import Job
from ...models.publish import (
    ACTIVE_PUBLISH_STATUSES,
    Publish,
    PublishKind,
    PublishStatus,
)
from .approval_digest import publish_request_digest
from .build_service import completed_build, version_or_refuse

#: A pending approval that has passed ``expires_at`` never runs (P-08); an approved one is used
#: while it executes (publish) or, for a send, after 009 executed it.
_USABLE = {"publish": ("executing",), "send": ("executing", "executed")}


@dataclass(frozen=True)
class OperatorOrigin:
    pass


@dataclass(frozen=True)
class PublishApproval:
    approval_id: str


@dataclass(frozen=True)
class SendApproval:
    approval_id: str
    send_id: str


Authorization = OperatorOrigin | PublishApproval | SendApproval


@dataclass(frozen=True)
class Who:
    who: str
    origin: str  # "operator" | "agent"


@dataclass(frozen=True)
class PublishRequest:
    version_id: str
    build_id: str
    repo_id: str
    visibility: str
    card_prose: str
    kind: str = PublishKind.PUBLISH
    parent_publish_id: str | None = None


class ApprovalMismatch(ForbiddenError):
    code = "approval_mismatch"


def request_digest(session: Session, req: PublishRequest) -> str:
    version = version_or_refuse(session, req.version_id)
    build = completed_build(session, req.build_id, version.id)
    assert build.files is not None
    return publish_request_digest(
        kind=req.kind,
        version_id=version.id,
        build_id=build.id,
        build_files=build.files,
        repo_id=req.repo_id,
        visibility=req.visibility,
        card_prose=req.card_prose,
    )


def _consumed(session: Session, approval_id: str, digest: str) -> bool:
    return (
        session.execute(
            select(Publish.id).where(
                Publish.approval_id == approval_id, Publish.request_digest == digest
            )
        ).first()
        is not None
    )


def check_authorization(
    session: Session, authorization: Authorization, digest: str, who: Who
) -> Approval | None:
    """Return the covering approval (None for an operator), or raise ``approval_mismatch``."""
    if isinstance(authorization, OperatorOrigin):
        if who.origin != "operator":
            raise ApprovalMismatch(
                "An agent-originated publish needs an approval that covers it.",
                details={"digest": digest},
            )
        return None
    approval = session.get(Approval, authorization.approval_id, populate_existing=True)
    if approval is None or approval.action != "hub_push":
        raise ApprovalMismatch("No hub_push approval covers this publish.")
    shape = "send" if isinstance(authorization, SendApproval) else "publish"
    if approval.status not in _USABLE[shape] or approval.decided_at is None:
        raise ApprovalMismatch(
            f"Approval {approval.id} is {approval.status}; it cannot authorize a publish.",
            details={"status": approval.status},
        )
    if shape == "publish":
        covered: Sequence[str] = [str(approval.payload.get("publish_digest"))]
    else:
        assert isinstance(authorization, SendApproval)
        if approval.payload.get("send_id") != authorization.send_id:
            raise ApprovalMismatch("The send approval belongs to another send.")
        covered = [str(d) for d in approval.payload.get("publish_digests") or []]
    if digest not in covered:
        raise ApprovalMismatch(
            "The approval does not cover this request: the visibility, card or build changed "
            "after it was approved. Ask for a new approval.",
            details={"digest": digest, "covered": list(covered)},
        )
    if _consumed(session, approval.id, digest):
        raise ApprovalMismatch(
            f"Approval {approval.id} has already been used for this publish.",
            details={"digest": digest},
        )
    return approval


def active_publish(session: Session, repo_id: str) -> Publish | None:
    return session.execute(
        select(Publish).where(
            Publish.repo_id == repo_id, Publish.status.in_(ACTIVE_PUBLISH_STATUSES)
        )
    ).scalar_one_or_none()


def _repo_busy(running: Publish | None, repo_id: str) -> ConflictError:
    name = running.id if running is not None else "another publish"
    return ConflictError(
        f"{name} is already publishing to {repo_id}. Wait for it to finish.",
        code="repo_busy",
        details={"repo_id": repo_id, "publish_id": running.id if running else None},
    )


@dataclass(frozen=True)
class PublishCreated:
    publish: Publish
    job: Job


def request_publish(
    session: Session,
    req: PublishRequest,
    authorization: Authorization,
    who: Who,
    *,
    send_id: str | None = None,
) -> PublishCreated:
    """Validate, authorize, and create the publish record and its job (not yet dispatched).

    ``send_id`` is 009's send for an OPERATOR send (``OperatorOrigin``); an agent send carries it
    in ``SendApproval``, which must agree. Either way the publish records the send it belongs to
    (009 FR-009.18, FTASKS 11.4).
    """
    if isinstance(authorization, SendApproval):
        if send_id is not None and send_id != authorization.send_id:
            raise ApprovalMismatch("The send approval belongs to another send.")
        send_id = authorization.send_id
    if req.visibility not in ("private", "public"):
        raise AppError(
            "visibility must be private or public", code="visibility_invalid", status_code=422
        )
    digest = request_digest(session, req)
    approval = check_authorization(session, authorization, digest, who)
    running = active_publish(session, req.repo_id)
    if running is not None:
        raise _repo_busy(running, req.repo_id)
    publish_id = new_id("pub")
    job = Job(
        id=new_id("job"),
        kind="publish",
        status="queued",
        progress=0.0,
        params={"publish_id": publish_id},
        started_by=who.who,
        started_by_origin=who.origin,
    )
    session.add(job)
    session.flush()
    publish = Publish(
        id=publish_id,
        job_id=job.id,
        version_id=req.version_id,
        build_id=req.build_id,
        kind=req.kind,
        parent_publish_id=req.parent_publish_id,
        repo_id=req.repo_id,
        requested_visibility=req.visibility,
        status=PublishStatus.QUEUED,
        card_prose=req.card_prose,
        request_digest=digest,
        approval_id=approval.id if approval is not None else None,
        send_id=send_id,
        approved_by=approval.decided_by if approval is not None else None,
        started_by=who.who,
        started_by_origin=who.origin,
    )
    session.add(publish)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if "uq_dw_publishes_active_repo" in str(exc.orig):
            raise _repo_busy(active_publish(session, req.repo_id), req.repo_id) from None
        raise
    return PublishCreated(publish, job)


def card_republish_request(session: Session, publish_id: str, card_prose: str) -> PublishRequest:
    """FR-008.56: a new card for a published publish, same build, repository and visibility."""
    parent = session.get(Publish, publish_id)
    if parent is None:
        raise NotFoundError(f"No publish {publish_id}.", code="publish_not_found")
    if parent.status != PublishStatus.PUBLISHED or parent.visibility_after is None:
        raise ConflictError(
            f"Publish {publish_id} is {parent.status}; only a published version's card can be "
            "republished.",
            code="publish_not_published",
        )
    return PublishRequest(
        version_id=parent.version_id,
        build_id=parent.build_id,
        repo_id=parent.repo_id,
        visibility=parent.visibility_after,
        card_prose=card_prose,
        kind=PublishKind.CARD_ONLY,
        parent_publish_id=parent.id,
    )


def get_publish(session: Session, publish_id: str) -> Publish:
    row = session.get(Publish, publish_id, populate_existing=True)
    if row is None:
        raise NotFoundError(f"No publish {publish_id}.", code="publish_not_found")
    return row


def latest_published(session: Session, version_id: str) -> Publish | None:
    return session.execute(
        select(Publish)
        .where(Publish.version_id == version_id, Publish.status == PublishStatus.PUBLISHED)
        .order_by(Publish.completed_at.desc(), Publish.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def published_manifest_for(session: Session, publish_id: str) -> dict[str, Any] | None:
    """FR-008.32: what 009 reads after its role publish completes."""
    row = get_publish(session, publish_id)
    return row.published_manifest if row.status == PublishStatus.PUBLISHED else None
