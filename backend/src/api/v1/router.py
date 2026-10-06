"""The router registry: the one list ``main.create_app`` includes (Foundation tasks 3.8, 11.4).

``tests/unit/test_reachability.py`` asserts every expected route is present in the LIVE app built
from this list, so deleting an entry here turns it red.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..internal import router as internal_router
from .endpoints.approvals import router as approvals_router
from .endpoints.endpoint_roles import router as endpoint_roles_router
from .endpoints.health import router as health_router
from .endpoints.jobs import router as jobs_router
from .endpoints.settings import router as settings_router

ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    jobs_router,
    settings_router,
    endpoint_roles_router,
    approvals_router,
    internal_router,
)
