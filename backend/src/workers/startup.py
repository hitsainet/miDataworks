"""Worker start-up: clear ``tmp/``, leave ``staging/`` alone (ADR-004; Foundation task 4.5)."""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import worker_ready

from ..core.config import get_settings
from ..core.logging import configure_logging, register_secret
from ..core.storage import clean_tmp_on_worker_start, ensure_layout

logger = logging.getLogger(__name__)


def on_worker_start() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    register_secret(settings.settings_encryption_key.get_secret_value())
    register_secret(settings.internal_api_secret.get_secret_value())
    ensure_layout()
    removed = clean_tmp_on_worker_start()
    logger.info("Worker start: removed %d entries from tmp/, left staging/ alone", removed)
    return removed


@worker_ready.connect
def _on_worker_ready(**_: Any) -> None:
    on_worker_start()
