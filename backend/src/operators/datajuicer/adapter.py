"""Data-Juicer adapter, backend side (FR-003.14; FTDD 003 section 6.4; FTID 003 section 3.8).

The registry reads Data-Juicer manifests from the committed catalogue (``catalogue.json``,
generated in the Data-Juicer image by ``datajuicer/scripts/build_catalogue.py``); the API never
imports the engine. This module turns a step into the runner's payload: plain JSON with no ``src``
types, because the runner lives in another image.
"""

from __future__ import annotations

from typing import Any

from ...core.config import get_settings
from ...services.operator_port import StepSpec


def runner_payload(entry: Any, spec: StepSpec) -> dict[str, Any]:
    """The ``midataworks.datajuicer.step`` payload for ``spec`` (paths relative to DATA_DIR)."""
    extra = entry.extra
    return {
        "op_name": extra["op_name"],
        "kind": entry.manifest.kind,
        "stats_key": extra.get("stats_key"),
        "params": dict(spec.params),
        "params_schema": entry.manifest.params_schema,
        "input_dir": spec.input_dir,
        "output_dir": raw_dir(spec),
        "job_id": spec.job_id,
        "step_execution_id": spec.step_execution_id,
        "num_proc": get_settings().dj_num_proc,
        "content_columns": sorted(c for c, r in spec.column_roles.items() if r == "content"),
    }


def raw_dir(spec: StepSpec) -> str:
    """Where the runner writes its output and raw decisions: beside the step's output dir."""
    return f"{spec.output_dir}.dj"
