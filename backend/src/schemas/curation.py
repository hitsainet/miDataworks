"""Feature 004's request bodies (FTDD 004 §4.2, §5.1). ``extra="forbid"``: a misspelled field is a
422, never silently ignored. Responses are the stored report documents plus the evaluated
warnings; their shapes are pinned by ``test_curation_routes.py`` and ``frontend/src/types/curation.ts``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputRef(_Body):
    version_id: str
    split: str | None = None
    role: str | None = Field(default=None, max_length=64)


class AuditRun(_Body):
    label_column: str | None = Field(default=None, min_length=1, max_length=255)
    #: Extra inputs for a cross-role audit (feature 009); the path's version is always first.
    inputs: list[InputRef] | None = Field(default=None, max_length=16)


class ProfileRun(_Body):
    sample_size: int | None = Field(default=None, gt=0, le=100_000)


class LeakageRun(_Body):
    inputs: list[InputRef] | None = Field(default=None, max_length=16)
    group_column: str | None = Field(default=None, min_length=1, max_length=255)
    threshold: float | None = Field(default=None, gt=0, le=1)


class ContaminationRun(_Body):
    benchmark_source_ids: list[str] = Field(min_length=1, max_length=20)
    n: int | None = Field(default=None, ge=3, le=50)


class ClustersRun(_Body):
    k: int | None = Field(default=None, ge=2, le=1000)


class TrlRun(_Body):
    target_type: str = Field(min_length=1, max_length=32)


class LevelSet(_Body):
    margin_pp: float
    reason: str | None = None


class LevelClear(_Body):
    reason: str | None = None


RunOutcome = Literal["existing", "inline", "started", "running"]
