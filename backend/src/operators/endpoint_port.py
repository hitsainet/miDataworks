"""This feature's view of feature 005: endpoint resolution and the miLLM lease (FR-003.4, FR-003.18).

Feature 005 owns ``resolve_endpoint(role)`` (FR-005.5) and the lease manager (FR-005.37; one lease
per model, X-08/T-21). Neither exists yet (task 1.1's named search, 2026-10-07: no
``resolve_endpoint`` and no lease manager under ``backend/src``). So, as ``services/operator_port``
does for 002, this module declares the two interfaces as protocols and holds ONE installed
implementation of each:

- until 005 lands, :class:`UnconfiguredResolver` refuses every role with ``endpoint_unconfigured``
  naming the Settings field (ADR-027: build nothing against an interface that is not served);
- :class:`NoLeaseManager` offers no lease, so every step is recorded as UNPINNED (P-13 allows an
  endpoint that offers no lease; the record says so rather than claiming a pin).

``RunContext.endpoint``, the executor's lease handling and the Data Designer relay read only these.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .errors import OperatorError


@dataclass(frozen=True)
class ResolvedEndpoint:
    """Where a role's requests go. ``api_key`` lives in memory only; never log or persist it."""

    role: str
    base_url: str
    model: str | None
    api_key: str | None = field(default=None, repr=False)
    #: True when the endpoint is miLLM: the relay then adds the lease and refuse-load headers.
    is_millm: bool = False


@dataclass(frozen=True)
class Lease:
    model: str
    lease_id: str
    revision: str | None = None


class EndpointResolver(Protocol):
    def resolve(self, role: str) -> ResolvedEndpoint: ...


class LeaseManager(Protocol):
    def acquire(self, endpoint: ResolvedEndpoint) -> Lease | None:
        """A lease on the endpoint's model, or None when the endpoint offers none (P-13)."""
        ...

    def renew(self, lease: Lease) -> None: ...

    def release(self, lease: Lease) -> None: ...


class UnconfiguredResolver:
    """Until feature 005 installs ``resolve_endpoint``: every role refuses, naming Settings."""

    def resolve(self, role: str) -> ResolvedEndpoint:
        raise OperatorError(
            "endpoint_unconfigured",
            f"No {role} endpoint can be resolved. Configure the {role} endpoint in Settings → "
            "Endpoints (feature 005 resolves endpoints for operators).",
            {"setting": f"endpoint_roles.{role}"},
        )


class NoLeaseManager:
    """Offers no lease: steps run unpinned and are recorded as such (P-13)."""

    def acquire(self, endpoint: ResolvedEndpoint) -> Lease | None:
        return None

    def renew(self, lease: Lease) -> None:  # pragma: no cover - never holds a lease
        return None

    def release(self, lease: Lease) -> None:  # pragma: no cover - never holds a lease
        return None


_resolver: EndpointResolver = UnconfiguredResolver()
_leases: LeaseManager = NoLeaseManager()


def install_resolver(resolver: EndpointResolver) -> EndpointResolver:
    global _resolver
    previous, _resolver = _resolver, resolver
    return previous


def install_lease_manager(manager: LeaseManager) -> LeaseManager:
    global _leases
    previous, _leases = _leases, manager
    return previous


def resolver() -> EndpointResolver:
    return _resolver


def lease_manager() -> LeaseManager:
    return _leases
