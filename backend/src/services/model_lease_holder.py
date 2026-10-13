"""The shared miLLM model lease: one per server, joined by every job on its model (FR-005.37 – 39).

X-08, T-21, FTDD 005 section 6.5. Used by 005, 007 and 009; nothing here is labeling-specific.

- :meth:`ModelLeaseHolder.join` takes a PostgreSQL advisory lock on the server, then: our active
  lease for this model → add a member (no miLLM call); otherwise read the resident model from
  ``GET /api/models`` (``server_probe.detect``) — not this model → :class:`ModelNotLoaded`
  (FR-005.38, nothing is loaded and nothing is asked to load); no lease surface →
  :class:`Unpinned` (P-13); else ``POST /api/models/{id}/lease``. Another holder →
  :class:`LeaseHeldElsewhere` (the job returns to ``queued`` and retries; never takes or breaks it).
- :meth:`check` at every chunk boundary renews once less than two thirds of the TTL remains; a
  ``404``/``409`` on renew marks the lease ``lost`` and raises ``LeaseLost``.
- :meth:`leave` on every terminal path; the last member releases the lease.
- :meth:`rejoin` after a miLLM restart (which ends every lease, miLLM FR-29.1.9; X-01): re-take
  the lease through :meth:`join`, then re-attach each running batch with
  ``POST /v1/batches/{id}/lease`` (FR-005.55). A refusal raises; no batch is ever resubmitted.
- :meth:`renew_all` is the Beat task's body: renew leases with live members, release leases whose
  members are all terminal (crash cleanup).

An agent-started job joins or takes the lease WITHOUT an approval (P-05): taking a lease loads
nothing. The lease ID is encrypted at rest, decrypted only here, never logged or returned.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..clients import millm_lease_client as lease_client
from ..clients.endpoint_caller import EndpointCaller, server_origin
from ..clients.endpoint_errors import LeaseLost, ModelNotResident
from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.encryption import decrypt_value, encrypt_value
from ..core.ids import new_id
from ..core.logging import register_secret
from ..models.job import TERMINAL_STATUSES, Job
from ..models.model_lease import ModelLease, ModelLeaseMember
from . import server_probe

logger = logging.getLogger(__name__)

_SETTING_KEY = "model_lease"


@dataclass(frozen=True)
class LeaseTicket:
    lease_row_id: str
    base_url: str
    model_name: str
    millm_model_id: int
    lease_id: str = field(repr=False)
    expires_at: datetime | None
    ttl_seconds: int


@dataclass(frozen=True)
class Unpinned:
    """The endpoint offers no lease: the run proceeds and is recorded ``pinned=false`` (P-13)."""

    reason: str


class ModelNotLoaded(Exception):
    code = "MODEL_NOT_LOADED"

    def __init__(self, needed: str, resident: str | None) -> None:
        super().__init__(
            f"{needed} is not loaded in miLLM ({resident or 'nothing'} is). Load {needed} in "
            "miLLM, then start the run."
        )
        self.needed = needed
        self.resident = resident


class LeaseHeldElsewhere(Exception):
    code = "LEASE_HELD"

    def __init__(self, holder: str | None, expires_at: str | None) -> None:
        super().__init__(
            f"miLLM's model is leased by {holder or 'another holder'} until {expires_at}. "
            "The run waits and retries; it never takes or breaks another holder's lease."
        )
        self.holder = holder
        self.expires_at = expires_at


SessionFactory = Callable[[], AbstractContextManager[Session]]


def _lock(session: Session, base_url: str) -> None:
    key = uuid.uuid5(uuid.NAMESPACE_URL, "midataworks-lease:" + base_url).int % (2**62)
    session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def _ticket(row: ModelLease) -> LeaseTicket:
    lease_id = decrypt_value(row.lease_id_ciphertext, setting_key=_SETTING_KEY)
    register_secret(lease_id)
    return LeaseTicket(
        row.id,
        row.base_url,
        row.model_name,
        row.millm_model_id,
        lease_id,
        row.expires_at,
        row.ttl_seconds,
    )


def _add_member(session: Session, lease_row_id: str, member_id: str) -> None:
    member = session.get(ModelLeaseMember, (lease_row_id, member_id))
    if member is None:
        session.add(ModelLeaseMember(lease_row_id=lease_row_id, member_id=member_id))
    else:
        member.left_at = None


def _sessions() -> AbstractContextManager[Session]:
    from ..core.database import get_sync_db

    return get_sync_db()


class ModelLeaseHolder:
    def __init__(self, sessions: SessionFactory = _sessions) -> None:
        self._sessions = sessions

    # --- join -------------------------------------------------------------------------------

    def join(
        self, caller: EndpointCaller, model_name: str, member_id: str
    ) -> LeaseTicket | Unpinned:
        settings = get_settings()
        base_url = server_origin(caller.base_url)
        with self._sessions() as session:
            _lock(session, base_url)
            active = session.execute(
                select(ModelLease)
                .where(ModelLease.base_url == base_url, ModelLease.state == "active")
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if active is not None:
                if active.model_name != model_name:
                    session.rollback()
                    raise ModelNotLoaded(model_name, active.model_name)
                _add_member(session, active.id, member_id)
                session.commit()
                logger.info("label_run.lease_joined lease=%s member=%s", active.id, member_id)
                return _ticket(active)
            info = server_probe.detect_server(caller)
            if not info.is_millm:
                session.rollback()
                return Unpinned(f"{info.kind} offers no model lease")
            if info.resident is None or info.resident.name != model_name:
                session.rollback()
                raise ModelNotLoaded(model_name, info.resident.name if info.resident else None)
            if not info.lease_supported:
                session.rollback()
                return Unpinned("this miLLM serves no lease routes")
            try:
                grant = lease_client.acquire(
                    caller,
                    info.resident.id,
                    holder=settings.millm_lease_holder,
                    reason=f"miDataworks jobs on {model_name}: {member_id}",
                    ttl_seconds=settings.millm_lease_ttl_seconds,
                )
            except lease_client.LeaseUnsupported:
                session.rollback()
                return Unpinned("this miLLM serves no lease routes")
            except lease_client.LeaseHeldElsewhere as held:
                session.rollback()
                raise LeaseHeldElsewhere(held.holder, held.expires_at) from None
            except ModelNotResident:
                session.rollback()
                raise ModelNotLoaded(model_name, None) from None
            register_secret(grant.lease_id)
            row = ModelLease(
                id=new_id("lease"),
                base_url=base_url,
                millm_model_id=info.resident.id,
                model_name=model_name,
                lease_id_ciphertext=encrypt_value(grant.lease_id),
                holder=settings.millm_lease_holder,
                reason=f"miDataworks jobs on {model_name}",
                ttl_seconds=grant.ttl_seconds or settings.millm_lease_ttl_seconds,
                expires_at=grant.expires_at
                or utc_now() + timedelta(seconds=settings.millm_lease_ttl_seconds),
                state="active",
            )
            session.add(row)
            session.flush()
            _add_member(session, row.id, member_id)
            session.commit()
            logger.info("label_run.lease_joined lease=%s member=%s acquired", row.id, member_id)
            return _ticket(row)

    # --- check and renew ----------------------------------------------------------------------

    def _mark_lost(self, session: Session, row: ModelLease, reason: str) -> None:
        row.state = "lost"
        row.lost_reason = reason
        row.ended_at = utc_now()
        session.commit()
        logger.warning("label_run.lease_lost lease=%s reason=%s", row.id, reason)

    def check(self, caller: EndpointCaller, ticket: LeaseTicket) -> LeaseTicket:
        """At a chunk boundary: still active, renewed when under two thirds of the TTL remains."""
        with self._sessions() as session:
            row = session.get(ModelLease, ticket.lease_row_id, populate_existing=True)
            if row is None or row.state != "active":
                reason = row.lost_reason if row is not None and row.lost_reason else "ended"
                raise LeaseLost(f"The miLLM lease is no longer held ({reason}).", reason)
            remaining = (row.expires_at - utc_now()).total_seconds()
            if remaining < row.ttl_seconds * 2 / 3:
                try:
                    expires = lease_client.renew(
                        caller, row.millm_model_id, ticket.lease_id, ttl_seconds=row.ttl_seconds
                    )
                except LeaseLost as lost:
                    self._mark_lost(session, row, lost.reason)
                    raise
                row.expires_at = expires or utc_now() + timedelta(seconds=row.ttl_seconds)
                session.commit()
            return _ticket(row)

    # --- leave ------------------------------------------------------------------------------

    def leave(self, caller: EndpointCaller, ticket: LeaseTicket, member_id: str) -> None:
        """Leave the lease; the last live member releases it. Safe on every terminal path."""
        with self._sessions() as session:
            _lock(session, ticket.base_url)
            member = session.get(ModelLeaseMember, (ticket.lease_row_id, member_id))
            if member is not None and member.left_at is None:
                member.left_at = utc_now()
            session.flush()
            row = session.get(ModelLease, ticket.lease_row_id, populate_existing=True)
            live = session.execute(
                select(ModelLeaseMember).where(
                    ModelLeaseMember.lease_row_id == ticket.lease_row_id,
                    ModelLeaseMember.left_at.is_(None),
                )
            ).first()
            if row is None or row.state != "active" or live is not None:
                session.commit()
                return
            self._release(session, caller, row, ticket.lease_id)

    def _release(
        self, session: Session, caller: EndpointCaller, row: ModelLease, lease_id: str
    ) -> None:
        try:
            lease_client.release(caller, row.millm_model_id, lease_id)
            row.state = "released"
        except LeaseLost as lost:
            row.state = "lost"
            row.lost_reason = lost.reason
        row.ended_at = utc_now()
        session.commit()
        logger.info("label_run.lease_released lease=%s state=%s", row.id, row.state)

    # --- re-attach after a restart (FR-005.55) ------------------------------------------------

    def rejoin(
        self,
        caller: EndpointCaller,
        ticket: LeaseTicket,
        member_id: str,
        batch_ids: Sequence[str],
    ) -> LeaseTicket:
        """Re-take the lease through :meth:`join`, then re-attach each running batch to it."""
        with self._sessions() as session:
            # A restart ends EVERY lease (miLLM FR-29.1.9), so any row we still hold active for
            # this server is stale — not only the ticket's: a join would otherwise re-use it.
            _lock(session, ticket.base_url)
            stale = session.execute(
                select(ModelLease)
                .where(ModelLease.base_url == ticket.base_url, ModelLease.state == "active")
                .execution_options(populate_existing=True)
            ).scalars()
            for row in list(stale):
                row.state = "lost"
                row.lost_reason = "restart"
                row.ended_at = utc_now()
            session.commit()
        joined = self.join(caller, ticket.model_name, member_id)
        if isinstance(joined, Unpinned):
            raise LeaseLost(f"The lease could not be re-taken: {joined.reason}.", "unpinned")
        self.reattach(caller, joined, batch_ids)
        return joined

    def reattach(
        self, caller: EndpointCaller, ticket: LeaseTicket, batch_ids: Sequence[str]
    ) -> None:
        """Hand each running batch the lease ``ticket`` holds (``POST /v1/batches/{id}/lease``).
        A refusal raises ``LeaseLost``; no batch is ever resubmitted."""
        for batch_id in batch_ids:
            try:
                lease_client.reattach_batch(caller, batch_id, ticket.lease_id)
            except lease_client.BatchReattachRefused as refused:
                raise LeaseLost(
                    f"miLLM refused to re-attach batch {batch_id} ({refused.status} "
                    f"{refused.code}); no batch is resubmitted.",
                    "reattach_refused",
                ) from None

    # --- Beat: renew and clean up -------------------------------------------------------------

    def active_leases(self) -> Iterator[tuple[str, str]]:
        with self._sessions() as session:
            rows = session.execute(
                select(ModelLease.id, ModelLease.base_url).where(ModelLease.state == "active")
            ).all()
        for lease_row_id, base_url in rows:
            yield str(lease_row_id), str(base_url)

    def renew_all(self, caller_for: Callable[[str], EndpointCaller]) -> dict[str, int]:
        """Release leases whose members are all terminal; renew the rest under 2/3 of the TTL."""
        released = checked = lost = 0
        for lease_row_id, base_url in list(self.active_leases()):
            caller = caller_for(base_url)
            try:
                with self._sessions() as session:
                    _lock(session, base_url)
                    row = session.get(ModelLease, lease_row_id, populate_existing=True)
                    if row is None or row.state != "active":
                        session.commit()
                        continue
                    members = list(
                        session.execute(
                            select(ModelLeaseMember).where(
                                ModelLeaseMember.lease_row_id == lease_row_id,
                                ModelLeaseMember.left_at.is_(None),
                            )
                        ).scalars()
                    )
                    for member in members:
                        if member.member_id.startswith("job_"):
                            job = session.get(Job, member.member_id, populate_existing=True)
                            if job is None or job.status in TERMINAL_STATUSES:
                                member.left_at = utc_now()
                    session.flush()
                    live = [m for m in members if m.left_at is None]
                    ticket = _ticket(row)
                    if not live:
                        self._release(session, caller, row, ticket.lease_id)
                        released += 1
                        continue
                    session.commit()
                try:
                    self.check(caller, ticket)
                    checked += 1
                except LeaseLost:
                    lost += 1
            finally:
                caller.close()
        return {"released": released, "checked": checked, "lost": lost}


HOLDER = ModelLeaseHolder()
