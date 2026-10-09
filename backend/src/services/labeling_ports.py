"""Feature 005 installs itself into the ports other features declared (FR-005.5, FR-005.37).

- 003's ``operators/endpoint_port``: :class:`PortResolver` replaces ``UnconfiguredResolver`` (the
  Data Designer relay and model-calling operators resolve roles through FR-005.5's one function),
  and :class:`PortLeaseManager` replaces ``NoLeaseManager`` (a step that needs the lease joins the
  shared holder, X-08).
- 002's binding resolvers: ``label_run`` bindings resolve to completed label runs.

:func:`install` runs at API start (the lifespan) and at worker start (``worker_process_init``). A
reachability test removes each call and requires a red.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.endpoint_errors import EndpointCallError
from ..core.errors import AppError
from ..models.label_run import LabelRun
from ..operators import endpoint_port
from ..operators.errors import OperatorError
from . import endpoint_resolver, server_probe
from .bindings import register_binding_resolver
from .label_run_service import binding_record, caller_for
from .model_lease_holder import HOLDER, LeaseTicket, ModelLeaseHolder
from .version_delete_service import Reference, ReferenceChecker, register_reference_checker

logger = logging.getLogger(__name__)


class PortResolver:
    """003's ``EndpointResolver`` on top of :func:`endpoint_resolver.resolve`."""

    def resolve(self, role: str) -> endpoint_port.ResolvedEndpoint:
        try:
            resolved = endpoint_resolver.resolve(role)
        except AppError as exc:
            raise OperatorError(
                "endpoint_unconfigured",
                f"No {role} endpoint can be resolved: {exc.message} (Settings → Endpoints).",
                {"setting": f"endpoint_roles.{role}"},
            ) from None
        try:
            with caller_for(resolved.base_url, resolved.api_key, transient_retries=0) as caller:
                info = server_probe.detect_server(caller)
        except EndpointCallError as exc:
            raise OperatorError(
                "endpoint_unreachable",
                f"The {role} endpoint at {resolved.base_url} did not answer: {exc.message}",
                {"role": role},
            ) from None
        return endpoint_port.ResolvedEndpoint(
            role=role,
            base_url=resolved.base_url,
            model=resolved.model_id,
            api_key=resolved.api_key,
            is_millm=info.is_millm,
        )


class PortLeaseManager:
    """003's ``LeaseManager`` on top of the shared holder. Each acquire joins as its own member."""

    def __init__(self, holder: ModelLeaseHolder = HOLDER) -> None:
        self.holder = holder
        self._members: dict[str, tuple[LeaseTicket, str, str, str | None]] = {}

    def acquire(self, endpoint: endpoint_port.ResolvedEndpoint) -> endpoint_port.Lease | None:
        if not endpoint.is_millm or endpoint.model is None:
            return None
        member = f"step:{uuid.uuid4().hex}"
        with caller_for(endpoint.base_url, endpoint.api_key) as caller:
            joined = self.holder.join(caller, endpoint.model, member)
        if not isinstance(joined, LeaseTicket):
            return None
        self._members[joined.lease_id] = (joined, member, endpoint.base_url, endpoint.api_key)
        return endpoint_port.Lease(model=endpoint.model, lease_id=joined.lease_id)

    def renew(self, lease: endpoint_port.Lease) -> None:
        entry = self._members.get(lease.lease_id)
        if entry is None:
            return
        ticket, _, base_url, api_key = entry
        with caller_for(base_url, api_key) as caller:
            self.holder.check(caller, ticket)

    def release(self, lease: endpoint_port.Lease) -> None:
        entry = self._members.pop(lease.lease_id, None)
        if entry is None:
            return
        ticket, member, base_url, api_key = entry
        with caller_for(base_url, api_key) as caller:
            self.holder.leave(caller, ticket, member)


async def _live_label_runs(db: AsyncSession, version_id: str) -> Reference | None:
    """002's delete guard: a version a queued or running label run reads cannot be deleted."""
    found = (
        await db.execute(
            select(LabelRun.id).where(
                LabelRun.input_version_id == version_id, LabelRun.state.in_(("queued", "running"))
            )
        )
    ).first()
    if found is None:
        return None
    return Reference(
        "version_in_use",
        f"Label run {found[0]} is labeling this version. Wait for it to finish or cancel it, then "
        "delete.",
        {"label_run_id": found[0]},
    )


def register_bindings() -> None:
    """Feature 005's registrations with feature 002: label-run bindings and the delete guard."""
    register_binding_resolver("label_run", binding_record)
    register_reference_checker(ReferenceChecker("005", "dw_label_runs", _live_label_runs))


def install() -> None:
    endpoint_port.install_resolver(PortResolver())
    endpoint_port.install_lease_manager(PortLeaseManager())
    register_bindings()
    logger.info("feature 005 installed: endpoint resolver, lease manager, label_run bindings")
