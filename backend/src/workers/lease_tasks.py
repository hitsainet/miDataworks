"""``midataworks.labeling.renew_model_leases`` (Beat, every 5 minutes; FTDD 005 section 6.5).

Renews shared miLLM leases that still have live members and releases leases whose members are all
terminal (crash cleanup, bounded by the 30-minute TTL). Routed to ``default``.
"""

from __future__ import annotations

from typing import Any

from ..core.celery_app import celery_app
from ..services.label_run_service import caller_for
from ..services.model_lease_holder import HOLDER


@celery_app.task(name="midataworks.labeling.renew_model_leases")
def renew_model_leases() -> dict[str, Any]:
    return HOLDER.renew_all(lambda base_url: caller_for(base_url, None))
