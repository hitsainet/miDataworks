"""The runner's configuration: ``RUNNER_*`` environment variables only (FTID 018 §3.2, §9).

A frozen dataclass, no pydantic-settings: the runner image stays small and the import guard simple.
Ceilings (``RUNNER_MAX_*``) bound what a caller may ask for; the floor comes from the contract
file, never from here (the runner carries no second copy of a contract value).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: The packaged contract, at the same place relative to ``src`` in the backend tree and the image.
DEFAULT_CONTRACT = (
    Path(__file__).resolve().parents[1] / "schemas" / "data" / "midataworks-runner-v1.json"
)


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class RunnerConfig:
    bundle_dir: Path
    contract_path: Path
    host: str
    port: int
    interactive_slots: int
    batch_slots: int
    batch_texts: int
    processes: int
    document_cache: int
    max_call_fuel: int
    max_load_fuel: int
    max_prelude_fuel: int
    max_memory_bytes: int
    max_input_code_points: int
    max_output_bytes: int
    max_wall_ms: int
    max_source_bytes: int

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> RunnerConfig:
        e = dict(os.environ if env is None else env)
        return cls(
            bundle_dir=Path(e.get("RUNNER_BUNDLE_DIR", "/app/bundle")),
            contract_path=Path(e.get("RUNNER_CONTRACT_PATH", str(DEFAULT_CONTRACT))),
            host=e.get("RUNNER_HOST", "0.0.0.0"),  # noqa: S104 - the Service's port
            port=_int(e, "RUNNER_PORT", 8080),
            interactive_slots=max(1, _int(e, "RUNNER_INTERACTIVE_SLOTS", 2)),
            batch_slots=max(1, _int(e, "RUNNER_BATCH_SLOTS", 4)),
            batch_texts=max(1, _int(e, "RUNNER_BATCH_TEXTS", 256)),
            processes=max(1, _int(e, "RUNNER_PROCESSES", 1)),
            document_cache=max(1, _int(e, "RUNNER_DOCUMENT_CACHE", 64)),
            max_call_fuel=_int(e, "RUNNER_MAX_CALL_FUEL", 1 << 35),
            max_load_fuel=_int(e, "RUNNER_MAX_LOAD_FUEL", 1 << 31),
            max_prelude_fuel=_int(e, "RUNNER_MAX_PRELUDE_FUEL", 1 << 31),
            max_memory_bytes=_int(e, "RUNNER_MAX_MEMORY_BYTES", 512 << 20),
            max_input_code_points=_int(e, "RUNNER_MAX_INPUT_CODE_POINTS", 1 << 20),
            max_output_bytes=_int(e, "RUNNER_MAX_OUTPUT_BYTES", 1 << 16),
            max_wall_ms=_int(e, "RUNNER_MAX_WALL_MS", 60_000),
            max_source_bytes=_int(e, "RUNNER_MAX_SOURCE_BYTES", 1 << 20),
        )
