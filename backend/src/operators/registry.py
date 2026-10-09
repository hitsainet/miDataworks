"""The operator registry (FR-003.10, FR-003.12, FR-003.13; FTDD 003 section 6.3; FTID 003 section 3.4).

Built once per process from four providers, in order:

1. native classes (``native.native_operators()``, the one list);
2. the Data Designer committed catalogue (manifests; implementations built lazily in the worker);
3. the Data-Juicer committed catalogue (manifests only; the engine runs in its own image);
4. entry points in ``midataworks.operators``, LISTED from metadata and imported only when the
   allowlist allows them (``plugins.load_allowed`` is the one importer).

Every manifest passes Pydantic and ``schema_subset.check``; a failure is the ``invalid_manifest``
state with the keyword named, never a silently rendered form. Duplicate ``name@version`` across
sources marks BOTH ``duplicate``. A thresholded operator without ``compute_statistics`` is
``invalid_manifest`` (FTASKS 9.3).

Entry-point operators' state is read from the allowlist on every check (never cached): a newly
allowed entry point is imported at its first use (lazy import), a revoked one is refused at once.
A revoked module cannot be unimported; both checks refuse it and the next restart drops it.

The registry implements feature 002's ``services.operator_port.OperatorRegistry``; it is installed
there by :func:`install_process_registry` from the app lifespan and Celery's worker start.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..services import operator_port
from ..services.operator_port import OperatorInfo, StepSpec
from . import plugins, schema_subset
from .errors import OperatorError, not_allowed, not_found
from .manifest import OperatorManifest, manifest_hash

logger = logging.getLogger(__name__)

STATES: tuple[str, ...] = (
    "allowed",
    "not_allowed",
    "invalid_manifest",
    "failed_to_load",
    "duplicate",
)
RUNNABLE = "allowed"

CATALOGUE_DIR = Path(__file__).resolve().parent
DJ_CATALOGUE = CATALOGUE_DIR / "datajuicer" / "catalogue.json"
DD_CATALOGUE = CATALOGUE_DIR / "data_designer" / "catalogue.json"

Triple = tuple[str, str, str]


@dataclass
class RegistryEntry:
    name: str
    version: str
    origin: str
    state: str
    manifest: OperatorManifest | None = None
    manifest_hash: str | None = None
    impl: Any = None
    error: str | None = None
    #: For entry-point operators (and unloaded entry points): the allowlist triple.
    entry_point: Triple | None = None
    #: Catalogue extras (Data-Juicer op name, statistic key, Data Designer column type).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def provider(self) -> str:
        if self.manifest is not None:
            return self.manifest.provider
        return self.origin


def _validate(manifest: OperatorManifest) -> str | None:
    violations = schema_subset.check(manifest.params_schema)
    if violations:
        return "params_schema uses " + schema_subset.describe(violations)
    return None


def _entry_from_manifest(
    raw: Any, origin: str, *, impl: Any = None, extra: dict[str, Any] | None = None
) -> RegistryEntry:
    """Validate one manifest (a model or a dict) into an entry; never raises."""
    try:
        manifest = (
            raw if isinstance(raw, OperatorManifest) else OperatorManifest.model_validate(raw)
        )
    except ValidationError as exc:
        name = raw.get("name", "?") if isinstance(raw, dict) else type(raw).__name__
        version = raw.get("version", "?") if isinstance(raw, dict) else "?"
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return RegistryEntry(
            str(name), str(version), origin, "invalid_manifest", error=f"{where}: {first['msg']}"
        )
    entry = RegistryEntry(
        manifest.name,
        manifest.version,
        origin,
        RUNNABLE,
        manifest=manifest,
        manifest_hash=manifest_hash(manifest),
        impl=impl,
        extra=dict(extra or {}),
    )
    problem = _validate(manifest)
    if problem is None and manifest.thresholds and impl is not None:
        if not callable(getattr(impl, "compute_statistics", None)):
            problem = (
                "the manifest declares thresholds but the operator has no compute_statistics(), "
                "so its threshold control could not be drawn (FR-003.21)"
            )
    if problem is not None:
        entry.state, entry.error = "invalid_manifest", problem
    return entry


def native_entries(classes: Iterable[type]) -> list[RegistryEntry]:
    entries = []
    for cls in classes:
        try:
            impl = cls()
            raw = impl.manifest
        except Exception as exc:  # noqa: BLE001 - one broken class must not stop the registry
            entries.append(
                RegistryEntry(cls.__name__, "?", "native", "failed_to_load", error=repr(exc))
            )
            continue
        entries.append(_entry_from_manifest(raw, "native", impl=impl))
    return entries


def catalogue_entries(path: Path, provider: str) -> list[RegistryEntry]:
    """Entries from a committed catalogue file; a missing file contributes nothing."""
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for item in document.get("operators", []):
        extra = {k: v for k, v in item.items() if k != "manifest"}
        entry = _entry_from_manifest(item["manifest"], f"catalogue:{provider}", extra=extra)
        if entry.manifest is not None and entry.manifest.provider != provider:
            entry.state, entry.error = "invalid_manifest", f"provider must be {provider}"
        entries.append(entry)
    return entries


def _plugin_entries(info: plugins.EntryPointInfo) -> list[RegistryEntry]:
    origin = f"plugin:{info.distribution}"
    try:
        operators = plugins.load_allowed(info)
    except Exception as exc:  # noqa: BLE001 - FR-003.10: one failure leaves the rest loaded
        logger.warning("entry point %s failed to load: %s", info.value, exc)
        return [
            RegistryEntry(
                info.name,
                info.distribution_version,
                origin,
                "failed_to_load",
                error=f"{type(exc).__name__}: {exc}",
                entry_point=info.triple,
            )
        ]
    entries = []
    for op in operators:
        entry = _entry_from_manifest(op.manifest, origin, impl=op)
        entry.entry_point = info.triple
        if entry.manifest is not None and entry.manifest.provider != origin:
            entry.state, entry.error = "invalid_manifest", f"provider must be {origin}"
        if entry.manifest is not None and entry.manifest.resources.queue in {
            "datajuicer",
            "designer",
        }:
            entry.state = "invalid_manifest"
            entry.error = "third-party operators run on curation or labeling (FR-003.16)"
        entries.append(entry)
    return entries


class OperatorRegistry:
    """Implements ``services.operator_port.OperatorRegistry`` (002) plus this feature's API."""

    def __init__(
        self,
        entries: Iterable[RegistryEntry],
        *,
        entry_points: Iterable[plugins.EntryPointInfo] = (),
        allowed_reader: Callable[[], set[Triple]] | None = None,
    ) -> None:
        self._entries: dict[str, RegistryEntry] = {}
        self._origins: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self._entry_points = {ep.triple: ep for ep in entry_points}
        self._loaded: set[Triple] = set()
        self._allowed_reader = allowed_reader
        for entry in entries:
            self._insert(entry)

    # --- building -----------------------------------------------------------------------------

    def _insert(self, entry: RegistryEntry) -> None:
        ref = entry.ref
        existing = self._entries.get(ref)
        self._origins.setdefault(ref, []).append(entry.origin)
        if existing is None:
            self._entries[ref] = entry
            return
        origins = ", ".join(self._origins[ref])
        for item in (existing, entry):
            item.state = "duplicate"
            item.error = f"{ref} is registered by more than one source ({origins}); neither runs."
        self._entries[ref] = existing

    @classmethod
    def build(
        cls,
        *,
        native: Iterable[type] | None = None,
        catalogues: Iterable[tuple[Path, str]] | None = None,
        entry_points: Iterable[plugins.EntryPointInfo] | None = None,
        allowed_reader: Callable[[], set[Triple]] | None = None,
    ) -> OperatorRegistry:
        """Build from the four providers (FTDD section 6.3). Defaults are the production ones."""
        from .native import native_operators

        if native is None:
            native = native_operators()
        if catalogues is None:
            catalogues = ((DD_CATALOGUE, "data_designer"), (DJ_CATALOGUE, "datajuicer"))
        if entry_points is None:
            entry_points = plugins.list_entry_points()
        entries: list[RegistryEntry] = []
        entries.extend(native_entries(native))
        for path, provider in catalogues:
            entries.extend(catalogue_entries(path, provider))
        registry = cls(entries, entry_points=entry_points, allowed_reader=allowed_reader)
        registry._load_plugins()
        return registry

    def _allowed(self) -> set[Triple]:
        if not self._entry_points:
            return set()  # no entry point installed: no database read at all
        if self._allowed_reader is None:
            from .allowlist import read_allowed

            return read_allowed()
        return self._allowed_reader()

    def _load_plugins(self) -> set[Triple]:
        """Import every entry point now allowed and not yet imported. Returns the allowed set."""
        allowed = self._allowed()
        with self._lock:
            for triple, info in self._entry_points.items():
                if triple in self._loaded or triple not in allowed:
                    continue
                self._loaded.add(triple)
                for entry in _plugin_entries(info):
                    self._insert(entry)
        return allowed

    # --- state --------------------------------------------------------------------------------

    def _state(self, entry: RegistryEntry, allowed: set[Triple]) -> str:
        if entry.state != RUNNABLE:
            return entry.state
        if entry.entry_point is not None and entry.entry_point not in allowed:
            return "not_allowed"
        return RUNNABLE

    def entries(self) -> list[tuple[RegistryEntry, str]]:
        """Every entry with its live state, plus one row per entry point not yet imported."""
        allowed = self._load_plugins()
        rows = [(e, self._state(e, allowed)) for e in self._entries.values()]
        for triple, info in sorted(self._entry_points.items()):
            if triple not in self._loaded:
                placeholder = RegistryEntry(
                    info.name,
                    info.distribution_version,
                    f"plugin:{info.distribution}",
                    "not_allowed",
                    entry_point=triple,
                    error="Not imported: allow this entry point to see its operators.",
                    extra={"entry_point_value": info.value},
                )
                rows.append((placeholder, "not_allowed"))
        return sorted(rows, key=lambda r: (r[0].name, r[0].version))

    def summary(self) -> dict[str, int]:
        counts = dict.fromkeys(STATES, 0)
        for _, state in self.entries():
            counts[state] += 1
        return counts

    def entry_points(self) -> list[plugins.EntryPointInfo]:
        return [self._entry_points[t] for t in sorted(self._entry_points)]

    def is_entry_point_allowed(self, triple: Triple) -> bool:
        """The allowlist's current answer for ``triple`` (read now, never cached)."""
        return triple in self._load_plugins()

    def operators_of(self, triple: Triple) -> list[RegistryEntry]:
        return [e for e in self._entries.values() if e.entry_point == triple]

    def entry(self, name: str, version: str) -> RegistryEntry:
        """The entry at exactly this version; ``operator_not_found`` or ``version_unavailable``."""
        self._load_plugins()
        found = self._entries.get(f"{name}@{version}")
        if found is not None:
            return found
        current = self.current_version(name)
        if current is not None:
            raise OperatorError(
                "version_unavailable",
                f"{name} {version} is not installed; the current version is {current}. Use "
                '"Clone recipe with current operators" to move to it (T-11).',
                {"operator": name, "version": version, "current_version": current},
            )
        raise not_found(name, version)

    def require_allowed(self, name: str, version: str) -> RegistryEntry:
        """The entry, or the refusal that stops a preview, a recipe save or a run (FR-003.12)."""
        entry = self.entry(name, version)
        state = self._state(entry, self._allowed())
        if state == RUNNABLE:
            return entry
        if state == "not_allowed":
            raise not_allowed(entry.ref, "its entry point is not on the allowlist")
        raise OperatorError(
            "operator_invalid_manifest",
            f"{entry.ref} cannot run: {state.replace('_', ' ')} ({entry.error}).",
            {"operator": entry.ref, "state": state, "error": entry.error},
        )

    # --- feature 002's port -------------------------------------------------------------------

    def get(self, name: str, version: str) -> OperatorInfo:
        entry = self.entry(name, version)
        if entry.manifest is None or entry.manifest_hash is None:
            raise OperatorError(
                "operator_invalid_manifest",
                f"{entry.ref} has no usable manifest: {entry.error}",
                {"operator": entry.ref, "state": entry.state},
            )
        return info_for(entry.manifest, entry.manifest_hash)

    def is_allowed(self, name: str, version: str) -> bool:
        try:
            self.require_allowed(name, version)
        except OperatorError:
            return False
        return True

    def current_version(self, name: str) -> str | None:
        allowed = self._allowed()
        runnable = [
            e.version
            for e in self._entries.values()
            if e.name == name and self._state(e, allowed) == RUNNABLE
        ]
        return runnable[-1] if runnable else None

    def validate_params_detailed(
        self, name: str, version: str, params: Any
    ) -> list[dict[str, str]]:
        entry = self.entry(name, version)
        if entry.manifest is None:
            raise OperatorError(
                "operator_invalid_manifest", f"{entry.ref} has no usable manifest: {entry.error}"
            )
        return schema_subset.validate(entry.manifest.params_schema, params)

    def validate_params(self, name: str, version: str, params: dict[str, Any]) -> list[str]:
        return [
            f"{e['pointer'] or '(params)'}: {e['message']}"
            for e in self.validate_params_detailed(name, version, params)
        ]

    def require_valid_params(self, name: str, version: str, params: Any) -> None:
        errors = self.validate_params_detailed(name, version, params)
        if errors:
            raise OperatorError(
                "params_invalid",
                f"{name} {version}: {len(errors)} parameter(s) do not match the operator's "
                "settings. Fix the marked fields.",
                {"errors": errors},
            )

    def implementation(self, entry: RegistryEntry) -> Any:
        """The object whose ``run`` executes ``entry`` in this process."""
        if entry.impl is not None:
            return entry.impl
        raise OperatorError(
            "worker_unavailable",
            f"{entry.ref} has no implementation in this process (provider {entry.provider}).",
            {"operator": entry.ref},
        )

    def dispatch_step(self, spec: StepSpec, link_task: str, link_args: list[Any]) -> None:
        from .executor import dispatch_step

        dispatch_step(self, spec, link_task, link_args)


def info_for(manifest: OperatorManifest, digest: str) -> OperatorInfo:
    """002's view of a manifest (``services.operator_port.OperatorInfo``)."""
    return OperatorInfo(
        name=manifest.name,
        version=manifest.version,
        manifest_hash=digest,
        kind=manifest.kind,
        provider=manifest.provider,
        queue=manifest.resources.queue,
        input_columns=tuple(c.name for c in manifest.input_columns if c.required),
        output_columns={c.name: (c.role or "metadata") for c in manifest.output_columns},
        endpoint_role=manifest.resources.endpoint_role,
        detector_labeler_kind=manifest.detector_labeler_kind,
        binding_kinds=manifest.binding_kinds,
    )


# --- the process registry ---------------------------------------------------------------------

_process: OperatorRegistry | None = None
_process_lock = threading.Lock()


def current() -> OperatorRegistry:
    """The process registry, built and installed on first use."""
    global _process
    if _process is None:
        with _process_lock:
            if _process is None:
                install_process_registry()
    assert _process is not None
    return _process


def install_process_registry(registry: OperatorRegistry | None = None) -> OperatorRegistry:
    """Build (or take) the registry and install it as 002's port. App lifespan and worker start."""
    global _process
    _process = registry or OperatorRegistry.build()
    operator_port.install_registry(_process)
    logger.info("operator registry installed: %s", _process.summary())
    return _process


def reset_process_registry() -> None:
    """Forget the process registry (tests)."""
    global _process
    _process = None


__all__ = [
    "OperatorRegistry",
    "RegistryEntry",
    "current",
    "info_for",
    "install_process_registry",
]
