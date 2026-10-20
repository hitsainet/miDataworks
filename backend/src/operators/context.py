"""The run context an operator sees (FR-003.4; FTID 003 section 3.3).

Constructed by the executor or the preview runner only. An operator reaches the seed, the cancel
check, progress, an endpoint, the sample flag, its input (dataset scope), scratch space and a
logger ONLY through this object; it never opens a database session or reads settings.

The event helpers fill the identity fields (operator ref, manifest hash, occurrence) so each
operator does not. New occurrences for changed and added rows come from ONE allocator per step,
seeded with the step's whole input identity set, so two batches can never hand out the same
``(row key, occurrence)`` pair (FR-002.21, FR-002.47).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from ..core.canonical_json import canonical_json
from ..services.row_keys import compute_row_key
from . import endpoint_port
from .endpoint_port import ResolvedEndpoint
from .errors import OperatorError
from .manifest import OperatorManifest
from .protocol import RowEvent

Pair = tuple[str, int]
InputReader = Callable[[list[str] | None], Iterator[pa.RecordBatch]]


class OccurrenceAllocator:
    """Hands out the smallest occurrence of a key not yet used in the step."""

    def __init__(self, taken: Iterable[Pair] = ()) -> None:
        self._taken: dict[str, set[int]] = {}
        for key, occurrence in taken:
            self._taken.setdefault(key, set()).add(int(occurrence))

    def next(self, key: str) -> int:
        used = self._taken.setdefault(key, set())
        occurrence = 0
        while occurrence in used:
            occurrence += 1
        used.add(occurrence)
        return occurrence


def _noop() -> None:
    return None


def _no_progress(done: int, total: int) -> None:
    return None


@dataclass
class RunContext:
    manifest: OperatorManifest
    manifest_hash: str
    step_seed: int
    job_id: str | None
    column_roles: dict[str, str]
    rowkey_scheme: str
    sample: bool = False
    step_execution_id: str | None = None
    check_cancel: Callable[[], None] = _noop
    progress_fn: Callable[[int, int], None] = _no_progress
    input_reader: InputReader | None = None
    tmp_dir: Path | None = None
    allocator: OccurrenceAllocator = field(default_factory=OccurrenceAllocator)
    #: Per-step fields merged into every forwarded model request (FR-003.26; Data Designer).
    body_overrides: dict[str, Any] | None = None
    #: Per-request records a model-calling operator appends (row key, status, model, revision,
    #: raw X-miLLM-Steering, latency); returned in StepResult.relay_records (FR-003.26).
    relay_records: list[dict[str, Any]] = field(default_factory=list)
    #: Feature 007: a lease the caller holds for a generation stage (sent as X-miLLM-Lease).
    lease_id: str | None = field(default=None, repr=False)
    #: The build's bound runs (``StepSpec.bindings``), so an operator reading a recorded run can
    #: refuse one the build did not bind (feature 007's ``dw_generated_rows``).
    bindings: list[dict[str, Any]] = field(default_factory=list)
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("src.operators.run"))

    # --- handles ------------------------------------------------------------------------------

    @cached_property
    def rng(self) -> np.random.Generator:
        """Seeded from the step seed (FR-002.30): same seed, same draws."""
        return np.random.default_rng(self.step_seed)

    @cached_property
    def endpoint(self) -> ResolvedEndpoint:
        """The endpoint for the role the manifest declares (FR-005.5), resolved on first use."""
        role = self.manifest.resources.endpoint_role
        if role is None:
            raise OperatorError(
                "endpoint_role_missing",
                f"{self.manifest.ref_text} asked for an endpoint but its manifest declares no "
                "endpoint role.",
                {"operator": self.manifest.ref_text},
            )
        return endpoint_port.resolver().resolve(role)

    def progress(self, done: int, total: int) -> None:
        self.progress_fn(done, total)

    @property
    def content_columns(self) -> list[str]:
        return sorted(c for c, role in self.column_roles.items() if role == "content")

    def row_key(self, row: Mapping[str, Any]) -> str:
        """Feature 002's row key of ``row`` over the step's content columns (FR-002.19)."""
        return compute_row_key(row, self.content_columns, self.rowkey_scheme)

    # --- events -------------------------------------------------------------------------------

    def _base(self) -> dict[str, Any]:
        return {
            "operator_name": self.manifest.name,
            "operator_version": self.manifest.version,
            "manifest_hash": self.manifest_hash,
        }

    @staticmethod
    def _pair(row: Mapping[str, Any] | Pair) -> Pair:
        if isinstance(row, tuple):
            return row[0], int(row[1])
        return str(row["_dw_row_key"]), int(row["_dw_occurrence"])

    @staticmethod
    def _threshold(value: Any, comparator: str | None) -> str | None:
        if value is None:
            return None
        return canonical_json({"value": value, "comparator": comparator or "?"}).decode("utf-8")

    def drop(
        self,
        row: Mapping[str, Any] | Pair,
        reason_code: str,
        reason: str,
        statistic: str | None,
        value: float | None = None,
        threshold: Any = None,
        comparator: str | None = None,
        *,
        kept_key: str | None = None,
        text: str | None = None,
    ) -> RowEvent:
        key, occurrence = self._pair(row)
        return RowEvent(
            kind="dropped",
            row_key=key,
            occurrence=occurrence,
            reason_code=reason_code,
            reason=reason,
            statistic_name=statistic,
            statistic_value=None if value is None else float(value),
            statistic_text=text,
            threshold=self._threshold(threshold, comparator),
            related_row_key=kept_key,
            **self._base(),
        )

    def change(
        self,
        row: Mapping[str, Any] | Pair,
        new_row_key: str,
        reason_code: str,
        reason: str,
        statistic: str,
        value: float | None = None,
        *,
        text: str | None = None,
    ) -> RowEvent:
        """A changed row. Its new occurrence comes from the step's allocator (or is kept when the
        key did not change). Write ``new_row_key`` / ``event.new_occurrence`` into the output row.
        """
        key, occurrence = self._pair(row)
        new_occurrence = occurrence if new_row_key == key else self.allocator.next(new_row_key)
        return RowEvent(
            kind="changed",
            row_key=key,
            occurrence=occurrence,
            new_row_key=new_row_key,
            new_occurrence=new_occurrence,
            reason_code=reason_code,
            reason=reason,
            statistic_name=statistic,
            statistic_value=None if value is None else float(value),
            statistic_text=text,
            **self._base(),
        )

    def add(
        self, new_row_key: str, parent_keys: Iterable[str], reason_code: str, reason: str
    ) -> RowEvent:
        """An added row (generators). Write ``row_key`` / ``event.occurrence`` into the new row."""
        return RowEvent(
            kind="added",
            row_key=new_row_key,
            occurrence=self.allocator.next(new_row_key),
            parent_keys=tuple(parent_keys),
            reason_code=reason_code,
            reason=reason,
            **self._base(),
        )

    def assign_split(
        self, row: Mapping[str, Any] | Pair, split: str, reason_code: str, reason: str
    ) -> RowEvent:
        key, occurrence = self._pair(row)
        return RowEvent(
            kind="split_assigned",
            row_key=key,
            occurrence=occurrence,
            split=split,
            reason_code=reason_code,
            reason=reason,
            **self._base(),
        )
