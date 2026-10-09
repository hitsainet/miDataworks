"""Feature 002's view of feature 003: the operator registry and the step executor (FR-002.12, 002.47).

002 calls exactly four things of 003 (003 FTID sections 3.4 and 3.6; 002 FTDD section 6.4):
``get``, ``is_allowed``, ``validate_params`` and ``dispatch_step``. They are declared here as a
protocol, and the process holds ONE registry, installed with :func:`install_registry`.

Until feature 003 lands, the installed registry is :class:`NoOperatorsInstalled`: every operator
lookup refuses with ``operator_not_found``, naming the reason. Nothing is guessed: a recipe that
names an operator nobody can run is refused at save time, not discovered at build time
(ADR-027: build nothing against an interface that is not served). Tests install the stubs in
``tests/support/stub_operators.py``, which obey FR-003.3; stubs never live in ``src/``.

**The step contract (C-002.10).** ``dispatch_step(spec, link_task, link_args)`` runs one operator
over the Parquet parts in ``spec.input_dir`` and writes into ``spec.output_dir`` (renamed into place
from staging): ``part-*.parquet`` carrying every column including the ``_dw_`` system columns,
``events.parquet`` (one row per dropped, changed, added or split-assigned row), and ``meta.json``
(the counts, ``output_column_roles``, optional ``split_roles``, optional ``error``). When the step
ends — success or failure — the executor sends ``link_task(*link_args)``. Conservation counts
``(row key, occurrence)`` pairs (FR-002.47).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class OperatorRefusal(Exception):
    """A per-step refusal from the registry; ``code`` is 003's code (002 FTDD section 5.7)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OperatorInfo:
    """What 002 needs from an operator's manifest (FR-003.1)."""

    name: str
    version: str
    manifest_hash: str
    #: filter, deduplicator, selector, mapper, labeler, generator, report, exporter (FR-003.5).
    kind: str
    provider: str = "native"
    #: The queue its step runs on (FR-003.17).
    queue: str = "curation"
    #: Columns the step reads; each must exist at that point in the pipeline (FR-002.12).
    input_columns: tuple[str, ...] = ()
    #: Columns the step adds, with their roles.
    output_columns: dict[str, str] = field(default_factory=dict)
    #: An endpoint role means the step calls a model: it counts toward P-07 (FR-002.50).
    endpoint_role: str | None = None
    #: Feature 009's probe-verdict or feature-tagging labelers count too (FR-002.50).
    detector_labeler_kind: str | None = None
    #: Binding kinds the step consumes; only those enter its identity (FR-002.28).
    binding_kinds: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def labels_rows(self) -> bool:
        return self.endpoint_role is not None or self.detector_labeler_kind is not None

    @property
    def run_kind(self) -> str:
        """The P-07 ledger's run kind for a labelling step (010 FTDD section 4.3)."""
        return self.detector_labeler_kind or "label_run"


@dataclass(frozen=True)
class StepSpec:
    """The payload 002 hands 003's executor (FTDD 002 section 6.4)."""

    step_execution_id: str
    operator: str
    version: str
    params: dict[str, Any]
    #: Relative to ``DATA_DIR``.
    input_dir: str
    output_dir: str
    step_seed: int
    job_id: str
    bindings: list[dict[str, Any]]
    #: Roles of the input's columns, so the executor keys changed and added rows (FR-002.19).
    column_roles: dict[str, str]
    rowkey_scheme: str
    expected_manifest_hash: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "step_execution_id": self.step_execution_id,
            "operator": self.operator,
            "version": self.version,
            "params": self.params,
            "input_dir": self.input_dir,
            "output_dir": self.output_dir,
            "step_seed": self.step_seed,
            "job_id": self.job_id,
            "bindings": self.bindings,
            "column_roles": self.column_roles,
            "rowkey_scheme": self.rowkey_scheme,
            "expected_manifest_hash": self.expected_manifest_hash,
        }


class OperatorRegistry(Protocol):
    def get(self, name: str, version: str) -> OperatorInfo:
        """The operator at exactly this version; ``OperatorRefusal('operator_not_found')``."""
        ...

    def is_allowed(self, name: str, version: str) -> bool: ...

    def current_version(self, name: str) -> str | None:
        """The single runnable version of ``name``, or None (T-11); never used to upgrade."""
        ...

    def validate_params(self, name: str, version: str, params: dict[str, Any]) -> list[str]:
        """Every problem with ``params`` against the operator's JSON Schema; empty when valid."""
        ...

    def dispatch_step(self, spec: StepSpec, link_task: str, link_args: list[Any]) -> None:
        """Queue the step; the executor sends ``link_task(*link_args)`` when it ends."""
        ...


class NoOperatorsInstalled:
    """The registry until feature 003 installs its own: it knows no operator."""

    REASON = (
        "No operator registry is installed: feature 003 (Operator Framework) provides the "
        "operators recipes run. Save the recipe as a draft until it lands."
    )

    def get(self, name: str, version: str) -> OperatorInfo:
        raise OperatorRefusal(
            "operator_not_found", f"Operator {name} {version} is not installed. {self.REASON}"
        )

    def is_allowed(self, name: str, version: str) -> bool:
        return False

    def current_version(self, name: str) -> str | None:
        return None

    def validate_params(self, name: str, version: str, params: dict[str, Any]) -> list[str]:
        return [self.REASON]

    def dispatch_step(self, spec: StepSpec, link_task: str, link_args: list[Any]) -> None:
        raise OperatorRefusal("operator_not_found", self.REASON)


_registry: OperatorRegistry = NoOperatorsInstalled()


def install_registry(registry: OperatorRegistry) -> OperatorRegistry:
    """Install the process's registry (feature 003 at start-up; tests). Returns the previous."""
    global _registry
    previous = _registry
    _registry = registry
    return previous


def registry() -> OperatorRegistry:
    return _registry
