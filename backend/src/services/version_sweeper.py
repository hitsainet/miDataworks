"""The version orphan sweeper (FR-002.35, FR-002.37; FTASKS 9.8).

Two rules, and nothing else is touched (``staging/`` never — a resumable job may own files there):
1. ``versions/<id>/`` with no ``dw_versions`` row, older than ``ORPHAN_SWEEP_AGE_MINUTES``: the
   rename happened and the commit did not (finalize renames BEFORE it commits).
2. ``versions/<id>/`` whose row is a tombstone: the delete committed and the file removal failed.
"""

from __future__ import annotations

import logging
import shutil
import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.storage import list_entries
from ..models.enums import VersionState
from ..models.version import Version

logger = logging.getLogger(__name__)


def sweep_orphans(session: Session, *, now: float | None = None) -> list[str]:
    """Remove orphaned and tombstoned version directories; return the names removed."""
    now = time.time() if now is None else now
    age = get_settings().orphan_sweep_age_minutes * 60
    removed: list[str] = []
    for entry in list_entries("versions"):
        if not entry.is_dir():
            continue
        try:
            version_id = str(uuid.UUID(entry.name))
        except ValueError:
            continue
        state = session.execute(
            select(Version.state).where(Version.id == version_id)
        ).scalar_one_or_none()
        if state is None:
            if now - entry.stat().st_mtime < age:
                continue
            reason = "no version row"
        elif state == VersionState.DELETED:
            reason = "version tombstoned"
        else:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry.name)
        logger.info("swept versions/%s (%s)", entry.name, reason)
    return removed
