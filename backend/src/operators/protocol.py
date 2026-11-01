"""The operator protocol, its result and its events (FR-003.3, FR-003.8; FTID 003 section 3.2).

``RowEvent`` mirrors feature 002's event record (``services/step_contract.EVENT_SCHEMA``, C-002.5)
plus the operator reference and manifest hash. Its constructor refuses an event without a reason
code or a reason, and a drop or change without a statistic name: a drop without a reason is a
contract violation, not a warning (FPRD 003 section 2.3). Operators build events through
``RunContext.drop`` / ``change`` / ``add`` / ``assign_split`` so the identity fields are filled by
the framework, not by each operator.

``OperatorResult.output`` holds the kept and changed rows, each carrying ``_dw_row_key`` and
``_dw_occurrence`` (for a changed row, the NEW pair its event names). ``added`` holds a
generator's new rows. ``report`` carries a report kind's artefact, and ``split_roles`` for a
selector with the ``assign_split`` effect (FR-003.27).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import pyarrow as pa

from .errors import OperatorError
from .manifest import OperatorManifest

if TYPE_CHECKING:
    from .context import RunContext

EventKind = Literal["dropped", "changed", "added", "split_assigned"]
EVENT_KINDS: frozenset[str] = frozenset({"dropped", "changed", "added", "split_assigned"})
#: Event kinds that must name the statistic that decided them (002 ingestion requires it too).
NEEDS_STATISTIC: frozenset[str] = frozenset({"dropped", "changed"})

#: The fields written to ``events.parquet`` (002's EVENT_SCHEMA, in its order).
EVENT_FILE_FIELDS: tuple[str, ...] = (
    "kind",
    "row_key",
    "occurrence",
    "new_row_key",
    "new_occurrence",
    "related_row_key",
    "parent_keys",
    "split",
    "reason_code",
    "reason",
    "statistic_name",
    "statistic_value",
    "statistic_text",
    "threshold",
)


@dataclass(frozen=True)
class RowEvent:
    kind: str
    row_key: str
    occurrence: int
    reason_code: str
    reason: str
    operator_name: str
    operator_version: str
    manifest_hash: str
    statistic_name: str | None = None
    statistic_value: float | None = None
    statistic_text: str | None = None
    #: JSON text ``{"value": ..., "comparator": ...}``.
    threshold: str | None = None
    new_row_key: str | None = None
    new_occurrence: int | None = None
    #: A deduplicator's drop names the row kept in its place.
    related_row_key: str | None = None
    parent_keys: tuple[str, ...] | None = None
    split: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in EVENT_KINDS:
            raise OperatorError("event_invalid", f"Unknown event kind {self.kind!r}.")
        if not self.reason_code or not self.reason_code.strip():
            raise OperatorError(
                "event_invalid",
                f"{self.operator_name}@{self.operator_version} emitted a {self.kind} event for "
                f"{self.row_key[:12]} with no reason code. Every drop, change and addition "
                "needs a reason (FR-003.8).",
                {"row_key": self.row_key, "occurrence": self.occurrence},
            )
        if not self.reason or not self.reason.strip():
            raise OperatorError(
                "event_invalid",
                f"{self.operator_name}@{self.operator_version} emitted a {self.kind} event for "
                f"{self.row_key[:12]} with no reason.",
                {"row_key": self.row_key, "occurrence": self.occurrence},
            )
        if self.kind in NEEDS_STATISTIC and not (self.statistic_name or "").strip():
            raise OperatorError(
                "event_invalid",
                f"{self.operator_name}@{self.operator_version} {self.kind} row "
                f"{self.row_key[:12]} without naming the statistic that decided it (FR-003.8).",
                {"row_key": self.row_key, "occurrence": self.occurrence},
            )
        if self.kind == "changed" and not self.new_row_key:
            raise OperatorError("event_invalid", "A changed event must carry the new row key.")
        if self.kind == "split_assigned" and not self.split:
            raise OperatorError("event_invalid", "A split_assigned event must name the split.")

    def file_row(self) -> dict[str, Any]:
        row = asdict(self)
        out = {name: row[name] for name in EVENT_FILE_FIELDS}
        out["parent_keys"] = list(self.parent_keys) if self.parent_keys is not None else None
        return out


@dataclass
class OperatorResult:
    output: pa.Table
    events: list[RowEvent] = field(default_factory=list)
    added: pa.Table | None = None
    report: dict[str, Any] | None = None
    #: Output column roles for columns the operator added (merged into the step's roles).
    output_roles: dict[str, str] | None = None


@runtime_checkable
class Operator(Protocol):
    manifest: OperatorManifest

    def run(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> OperatorResult: ...


@runtime_checkable
class StatisticsOperator(Protocol):
    """Required when ``manifest.thresholds`` is non-empty (FTDD 003 section 6.2)."""

    def compute_statistics(
        self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext
    ) -> dict[str, pa.Array]: ...
