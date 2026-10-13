"""Detector-set routes (FTDD 009 section 5.1): sets, checks, sends, results.

- Every write resolves who through ``resolve_who`` (C5); a who in a body is refused by
  ``extra="forbid"``.
- The services are synchronous on a ``Session`` and run through ``AsyncSession.run_sync``, so a
  route and the send worker share one code path. Calls to miStudio run in a worker thread.
- ``POST /detector-sets/{id}/send`` is gated for agents as ``hub_push`` (P-06): one approval covers
  the whole send. The stored request carries the send's ID and its approval digest (FR-009.82); the
  approved replay recomputes the plan and refuses ``approval_mismatch`` when it changed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....schemas.detector_sets import (
    AgreementReportIn,
    DetectorSetCreate,
    DetectorSetUpdate,
    ReproductionLinkIn,
    RewardMarkIn,
    SendRequest,
)
from ....services.detector_sets import integration, set_service
from ....services.detector_sets.set_service import Who
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["detector-sets"])


async def _who(actor: Actor, db: AsyncSession) -> Who:
    resolved = await resolve_who(actor, db)
    return Who(resolved.who, resolved.origin)


@router.post("/detector-sets", status_code=201)
async def create_detector_set(
    body: DetectorSetCreate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Create a detector set: four kinds of role bound to version splits (FR-009.1 - FR-009.8)."""
    who = await _who(actor, db)
    data = body.model_dump(mode="json")

    def run(s: Any) -> dict[str, Any]:
        return set_service.set_out(s, set_service.create_set(s, data, who))

    return await db.run_sync(run)


@router.get("/detector-sets")
async def list_detector_sets(
    archived: bool | None = Query(None),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Sets with their role summary, last send state and last result rung (FR-009.13)."""

    def run(s: Any) -> dict[str, Any]:
        rows, total = set_service.list_sets(s, page=page.page, limit=page.limit, archived=archived)
        return {
            "items": [set_service.set_summary(s, r) for r in rows],
            "total": total,
            "page": page.page,
            "limit": page.limit,
        }

    return await db.run_sync(run)


@router.get("/detector-sets/{set_id}")
async def get_detector_set(set_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A set with its roles, cached length profiles, sends and latest results."""

    def run(s: Any) -> dict[str, Any]:
        from ....services.detector_sets import send_service

        out = set_service.set_out(s, set_service.get_set(s, set_id))
        out["sends"] = [send_service.send_summary(x) for x in send_service.sends_of(s, set_id)]
        return out

    return await db.run_sync(run)


@router.patch("/detector-sets/{set_id}")
async def update_detector_set(
    set_id: str,
    body: DetectorSetUpdate,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Change any set or role field. Previous sends keep their snapshots (FR-009.12)."""
    await _who(actor, db)
    data = body.model_dump(mode="json", exclude_unset=True)

    def run(s: Any) -> dict[str, Any]:
        return set_service.set_out(s, set_service.update_set(s, set_id, data))

    return await db.run_sync(run)


@router.post("/detector-sets/{set_id}/archive")
async def archive_detector_set(
    set_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Archive a set. A set referenced by a send is never deleted (FR-009.13)."""
    await _who(actor, db)

    def run(s: Any) -> dict[str, Any]:
        return set_service.set_out(s, set_service.archive_set(s, set_id))

    return await db.run_sync(run)


@router.post("/detector-sets/{set_id}/checks")
async def check_detector_set(set_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """D-1 to D-8 with reasons and next steps, the label values each mapping must cover, and the
    length profiles (FR-009.5, FR-009.9, FR-009.14)."""
    return await db.run_sync(lambda s: set_service.run_checks(s, set_id))


# --- sends -------------------------------------------------------------------------------------


async def _send_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    """Stored inside the approval's digest: the send's future ID and its approval digest
    (FR-009.82), and the 008 publish digests the approval covers (P-06)."""
    from ....services.detector_sets import send_service

    body: SendRequest = values["body"]
    who = Who("agent", "agent")
    return await db.run_sync(
        lambda s: send_service.approval_facts(
            s,
            values["set_id"],
            explicit_repos=body.repositories,
            namespace=body.namespace,
            visibility=body.visibility,
            who=who,
        )
    )


def _send_summary(payload: dict[str, Any]) -> str:
    return (
        f"Send detector set {payload.get('set_name')} to miStudio: publish "
        f"{len(payload.get('repositories') or {})} version(s) "
        f"({payload.get('visibility')}) and register {payload.get('registrations')} view(s)."
    )


@router.post("/detector-sets/{set_id}/send", status_code=202)
@requires_approval_when_agent("hub_push", enrich=_send_facts, summary=_send_summary)
async def send_detector_set(
    set_id: str,
    body: SendRequest,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Publish each version, download it into miStudio and register every role (FR-009.17 -
    FR-009.25). Operator: the job starts at once. Agent: ``202`` with an approval ID (P-06)."""
    from ....services.detector_sets import send_service

    who = await _who(actor, db)

    def run(s: Any) -> dict[str, Any]:
        send = send_service.start(
            s,
            set_id,
            explicit_repos=body.repositories,
            namespace=body.namespace,
            visibility=body.visibility,
            who=who,
            approval_id=actor.approval_id,
        )
        return {"id": send.id, "send_id": send.id, "job_id": send.job_id, "state": send.state}

    return await db.run_sync(run)


@router.get("/detector-sets/{set_id}/sends")
async def list_detector_sends(set_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from ....services.detector_sets import send_service

    def run(s: Any) -> dict[str, Any]:
        set_service.get_set(s, set_id)
        return {"items": [send_service.send_summary(x) for x in send_service.sends_of(s, set_id)]}

    return await db.run_sync(run)


@router.get("/detector-sends/{send_id}")
async def get_detector_send(send_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A send with its snapshot, checks, steps and the run-request skeleton (FR-009.29)."""
    from ....services.detector_sets import send_service

    return await db.run_sync(lambda s: send_service.send_out(s, send_service.get_send(s, send_id)))


@router.post("/detector-sends/{send_id}/resume", status_code=202)
async def resume_detector_send(
    send_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """A new job for the same send; recorded steps are never repeated (FR-009.27)."""
    from ....services.detector_sets import send_service

    who = await _who(actor, db)

    def run(s: Any) -> dict[str, Any]:
        send = send_service.resume(s, send_id, who)
        return {"send_id": send.id, "job_id": send.job_id, "state": send.state}

    return await db.run_sync(run)


@router.post("/detector-sends/{send_id}/cancel")
async def cancel_detector_send(
    send_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Cancel the send's job; it stops at the next step boundary (ADR-007)."""
    from ....services.detector_sets import send_service
    from ....services.job_service import JobService

    await _who(actor, db)
    send = await db.run_sync(lambda s: send_service.get_send(s, send_id))
    job, detail = await JobService.cancel(db, send.job_id, "cancelled from the detector set")
    if job.status == "cancelled":
        await db.run_sync(lambda s: send_service.mark(s, send_id, "cancelled"))
    return {"send_id": send_id, "job_id": job.id, "status": job.status, "detail": detail}


# --- results -----------------------------------------------------------------------------------


@router.post("/detector-sets/{set_id}/results/refresh")
async def refresh_probe_results(
    set_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Read miStudio's runs, probes and reports for the set's latest send into a new snapshot
    (FR-009.31 - FR-009.41). miStudio unreachable: ``502``, the previous snapshot is kept."""
    import anyio

    from ....clients import mistudio_client as mc
    from ....services.detector_sets import results_service
    from ....services.detector_sets.errors import DetectorSetError

    integration.require_mistudio("Refreshing results")
    who = await _who(actor, db)

    def view_of(s: Any) -> Any:
        set_service.get_set(s, set_id)
        return results_service.send_view(s, results_service.latest_completed_send(s, set_id))

    view = await db.run_sync(view_of)

    def fetch() -> Any:
        with mc.MiStudioClient(view.base_url) as client:
            return results_service.collect(client, view)

    try:
        collected = await anyio.to_thread.run_sync(fetch)
    except mc.MiStudioNotJson as exc:
        raise DetectorSetError("mistudio_not_json", exc.message) from exc
    except mc.MiStudioError as exc:
        raise DetectorSetError(
            "mistudio_unreachable",
            f"{exc.message} The previous snapshot is kept.",
            {"status": exc.status},
        ) from exc
    return await db.run_sync(
        lambda s: results_service.results_out(
            results_service.store(s, set_id, view, collected, who)
        )
    )


@router.get("/detector-sets/{set_id}/results")
async def get_probe_results(
    set_id: str,
    snapshot: str = Query("latest", max_length=64),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """A stored results snapshot; reward-marked probes are never in ``evaluations`` (FR-009.70)."""
    from ....services.detector_sets import results_service

    return await db.run_sync(
        lambda s: results_service.results_out(results_service.snapshot(s, set_id, snapshot))
    )


# --- reward marks and agreement reports ---------------------------------------------------------


@router.post("/reward-marks", status_code=201)
async def mark_reward_probe(
    body: RewardMarkIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Mark a miStudio probe as used as a training reward. There is no unmark (TQ9)."""
    from ....services.detector_sets import reward_marks

    base_url = integration.require_mistudio("Marking a probe as a training reward")
    who = await _who(actor, db)

    def run(s: Any) -> dict[str, Any]:
        return reward_marks.mark_out(
            reward_marks.mark(
                s,
                base_url=base_url,
                probe_id=body.mistudio_probe_id,
                reason=body.reason,
                who=who,
                source=who.origin,
            )
        )

    return await db.run_sync(run)


@router.get("/reward-marks")
async def list_reward_marks(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from ....services.detector_sets import reward_marks

    return await db.run_sync(
        lambda s: {"items": [reward_marks.mark_out(m) for m in reward_marks.list_marks(s)]}
    )


@router.post("/agreement-reports", status_code=201)
async def create_agreement_report(
    body: AgreementReportIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Probe and judge on the same rows: two AUROCs, agreement, kappa (FR-009.52 - FR-009.56). No
    miStudio judge run is started (T-48)."""
    from ....core.agent_origin import Who as OriginWho
    from ....schemas.review import LabelReviewQueueCreate
    from ....services.detector_sets import agreement
    from ....services.review import queue_service

    who = await _who(actor, db)
    data = body.model_dump(mode="json")
    out = await db.run_sync(lambda s: agreement.report_out(agreement.compute(s, data, who)))
    if body.send_disagreements_to_review and out["figures"].get("disagreements"):
        queue = await queue_service.create_label_review(
            db,
            LabelReviewQueueCreate(
                kind="label_review",
                label_run_id=body.judge_label_run_id,
                row_keys=out["figures"]["disagreements"],
            ),
            OriginWho(who.who, who.origin),  # type: ignore[arg-type]
        )
        out["review_queue_id"] = queue.id
    return out


@router.get("/agreement-reports/{report_id}")
async def get_agreement_report(
    report_id: str, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    from ....services.detector_sets import agreement

    return await db.run_sync(lambda s: agreement.report_out(agreement.get(s, report_id)))


# --- reproduction links (FR-009.77 option (b), operator decision 2026-10-07) --------------------


def _link_summary(payload: dict[str, Any]) -> str:
    body = payload.get("body") or {}
    return (
        f"Link version {body.get('version_id')} split {body.get('split')!r} to miStudio's "
        f"evaluation of probe {body.get('mistudio_probe_id')} on view "
        f"{body.get('probe_dataset_id')}: the reproduction gate would compare miLLM's AUROC with "
        "that recorded interval."
    )


@router.post(
    "/reproduction-links",
    status_code=201,
    responses={202: {"description": "An agent's request waits for approval (gate_target_write)"}},
)
@requires_approval_when_agent("gate_target_write", summary=_link_summary)
async def create_reproduction_link(
    body: ReproductionLinkIn, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Link a version split to an evaluation miStudio already recorded for a probe, so the
    reproduction gate can use it for an imported probe. The rows are checked now (count, class
    balance, and a content hash when miStudio serves them); rows that provably differ are refused
    and nothing is stored. An agent's link waits for the operator (``gate_target_write``): like a
    calibration gate target, it sets what a gate compares against."""
    import anyio

    from ....clients import mistudio_client as mc
    from ....core.config import get_settings
    from ....services.detector_sets import reproduction_links
    from ....services.detector_sets.errors import DetectorSetError
    from ....services.sources.source_service import approval_ref

    who = await _who(actor, db)
    base_url = get_settings().mistudio_base_url
    if not base_url:
        raise DetectorSetError(
            "mistudio_not_configured",
            "A reproduction link reads the evaluation miStudio recorded, and miStudio is not "
            "configured (MISTUDIO_BASE_URL). Set it in the deployment configuration; everything "
            "else in miDataworks works without it.",
            {"setting": "MISTUDIO_BASE_URL"},
        )
    approval = await approval_ref(db, actor.approval_id)

    def fetch() -> Any:
        with mc.MiStudioClient(base_url) as client:
            return reproduction_links.read_mistudio(
                client, body.mistudio_probe_id, body.probe_dataset_id
            )

    try:
        side = await anyio.to_thread.run_sync(fetch)
    except mc.MiStudioNotJson as exc:
        raise DetectorSetError("mistudio_not_json", exc.message) from exc
    except mc.MiStudioError as exc:
        raise DetectorSetError(
            "mistudio_unreachable",
            f"{exc.message} Nothing was linked.",
            {"status": exc.status},
        ) from exc
    data = body.model_dump(mode="json")

    def run(s: Any) -> dict[str, Any]:
        row = reproduction_links.create(
            s,
            data,
            side,
            who,
            (approval.approval_id, approval.approved_by) if approval else None,
        )
        return reproduction_links.link_out(s, row)

    return await db.run_sync(run)


@router.get("/reproduction-links")
async def list_reproduction_links(
    mistudio_probe_id: str | None = Query(None, max_length=128),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Every reproduction link, newest first, optionally for one miStudio probe."""
    from ....services.detector_sets import reproduction_links

    return await db.run_sync(
        lambda s: {
            "items": [
                reproduction_links.link_out(s, r)
                for r in reproduction_links.list_links(s, mistudio_probe_id)
            ]
        }
    )


@router.get("/reproduction-links/{link_id}")
async def get_reproduction_link(link_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from ....services.detector_sets import reproduction_links

    return await db.run_sync(
        lambda s: reproduction_links.link_out(s, reproduction_links.get(s, link_id))
    )


@router.delete("/reproduction-links/{link_id}", status_code=204)
async def delete_reproduction_link(
    link_id: str, db: AsyncSession = Depends(get_db), actor: Actor = Depends(get_actor)
) -> None:
    """Delete a link no label run has used; a used link is immutable evidence (``link_in_use``)."""
    from ....services.detector_sets import reproduction_links

    await _who(actor, db)
    await db.run_sync(lambda s: reproduction_links.delete(s, link_id))
