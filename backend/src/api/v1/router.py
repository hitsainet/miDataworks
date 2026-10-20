"""The router registry: the one list ``main.create_app`` includes (Foundation tasks 3.8, 11.4).

``tests/unit/test_reachability.py`` asserts every expected route is present in the LIVE app built
from this list, so deleting an entry here turns it red.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..internal import router as internal_router
from .endpoints.agent_access import router as agent_access_router
from .endpoints.approvals import router as approvals_router
from .endpoints.audits import router as audits_router
from .endpoints.calibration import router as calibration_router
from .endpoints.config_versions import router as config_versions_router
from .endpoints.curation import router as curation_router
from .endpoints.datasets import router as datasets_router
from .endpoints.decision_templates import router as decision_templates_router
from .endpoints.detector_sets import router as detector_sets_router
from .endpoints.drafts import router as drafts_router
from .endpoints.endpoint_roles import router as endpoint_roles_router
from .endpoints.exports import router as exports_router
from .endpoints.generation import router as generation_router
from .endpoints.health import router as health_router
from .endpoints.jobs import router as jobs_router
from .endpoints.label_runs import router as label_runs_router
from .endpoints.labeling import router as labeling_router
from .endpoints.minimal_pairs import router as minimal_pairs_router
from .endpoints.model_terms import router as model_terms_router
from .endpoints.operators import router as operators_router
from .endpoints.publishing import router as publishing_router
from .endpoints.recipes import router as recipes_router
from .endpoints.review import router as review_router
from .endpoints.rubrics import router as rubrics_router
from .endpoints.settings import router as settings_router
from .endpoints.sources import router as sources_router
from .endpoints.versions import router as versions_router

ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    jobs_router,
    # Feature 004 BEFORE settings: /settings/shortcut-level must not fall into /settings/{key}.
    curation_router,
    settings_router,
    endpoint_roles_router,
    approvals_router,
    agent_access_router,
    datasets_router,
    recipes_router,
    drafts_router,
    versions_router,
    sources_router,
    operators_router,
    publishing_router,
    exports_router,
    model_terms_router,
    config_versions_router,
    decision_templates_router,
    rubrics_router,
    labeling_router,
    label_runs_router,
    generation_router,
    calibration_router,
    review_router,
    audits_router,
    detector_sets_router,
    minimal_pairs_router,  # feature 009: minimal pairs as a chain (decision 2026-10-07)
    internal_router,
)
