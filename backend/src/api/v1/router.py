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
from .endpoints.behaviours import router as behaviours_router
from .endpoints.calibration import router as calibration_router
from .endpoints.code_rules import router as code_rules_router
from .endpoints.config_versions import router as config_versions_router
from .endpoints.curation import router as curation_router
from .endpoints.datasets import router as datasets_router
from .endpoints.decision_templates import router as decision_templates_router
from .endpoints.detector_sets import router as detector_sets_router
from .endpoints.differences import router as differences_router
from .endpoints.downloads import router as downloads_router
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
from .endpoints.purge import router as purge_router
from .endpoints.recipes import router as recipes_router
from .endpoints.results import router as results_router
from .endpoints.review import router as review_router
from .endpoints.rubrics import router as rubrics_router
from .endpoints.rule_derivations import router as rule_derivations_router
from .endpoints.rules import rule_sets_router, validators_router
from .endpoints.runner import router as runner_router
from .endpoints.settings import router as settings_router
from .endpoints.settings_pin import router as settings_pin_router
from .endpoints.sources import router as sources_router
from .endpoints.versions import router as versions_router

ROUTERS: tuple[APIRouter, ...] = (
    health_router,
    jobs_router,
    # Feature 004 BEFORE settings: /settings/shortcut-level must not fall into /settings/{key}.
    curation_router,
    # Feature 014 BEFORE settings: /settings/pin must not fall into /settings/{key}.
    settings_pin_router,
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
    downloads_router,  # signed split downloads (PII run G8, 2026-10-21)
    purge_router,  # operator-only permanent delete of archived items (2026-10-20)
    validators_router,  # feature 016: the rule-signal validator library
    rule_sets_router,  # feature 016 slice 2: rule sets
    code_rules_router,  # feature 018 slice 3: operator code rules
    runner_router,  # feature 018 slice 3: the runner contract as served here
    rule_derivations_router,  # feature 016 slice 4: derived rule sets
    behaviours_router,  # feature 017: the catalogue read (resolve) and the status (11.4)
    results_router,  # feature 017 Part H: large result tables, paged and downloadable
    differences_router,  # feature 017 Part H: what a clone changed
    internal_router,
)
