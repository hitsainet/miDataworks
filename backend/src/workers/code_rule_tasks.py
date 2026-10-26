"""Beat task: re-check stored code rules under the runner's current contract (feature 018;
FR-018.31; FTDD §8.5). Every 10 minutes on ``default``; writes one ``dw_code_rule_contract_runs``
row per stored hash whose latest row is for another contract version. The runner unreachable or
unconfigured checks nothing and says so; the next pass retries."""

from __future__ import annotations

from typing import Any

from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..core.errors import AppError
from ..services.code_rules import contract_runs, limits


@celery_app.task(name="midataworks.code_rules.recheck_contract")
def recheck_contract_task() -> dict[str, Any]:
    try:
        client = limits.sync_client()
    except AppError as exc:  # RUNNER_URL unset: nothing to check against
        return {"checked": 0, "failing": 0, "runner": exc.code}
    with get_sync_db() as session:
        return contract_runs.recheck(session, client)
