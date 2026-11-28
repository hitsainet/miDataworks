"""Generate the committed Data-Juicer catalogue (FR-003.14; FTDD 003 sections 4.2 and 6.4).

Runs ONLY in the Data-Juicer image (or its CI job), never in the backend: it imports
``data_juicer``. For every Data-Juicer operator it derives a parameter schema from the operator's
``__init__`` signature (annotations and defaults, through pydantic) and checks it against the
supported subset, using ``schema_subset.py`` — a BYTE COPY of the backend module, because this
image cannot import ``src``. Operators named in ``allowlist.yaml`` that pass are OFFERED, with the
statistic key and threshold map the allowlist states; every other operator is listed under
``rejected`` with its reason, so the catalogue states how many exist and why each is excluded.

The output is written with canonical JSON (sorted keys, compact) plus a final newline, the same
bytes ``test_catalogue_in_sync.py`` regenerates and compares.

Usage::

    python datajuicer/scripts/build_catalogue.py            # write the catalogue
    python datajuicer/scripts/build_catalogue.py --check    # exit 1 if the committed file differs
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import schema_subset  # noqa: E402  (the byte copy beside this directory)

ALLOWLIST = ROOT / "allowlist.yaml"
OUTPUT = ROOT.parent / "backend" / "src" / "operators" / "datajuicer" / "catalogue.json"
#: Data-Juicer parameters that are runtime plumbing, not operator settings.
PLUMBING = frozenset(
    {
        "self",
        "args",
        "kwargs",
        "text_key",
        "image_key",
        "audio_key",
        "video_key",
        "query_key",
        "response_key",
        "history_key",
        "index_key",
        "batch_size",
        "num_proc",
        "cpu_required",
        "mem_required",
        "gpu_required",
        "turbo",
        "skip_op_error",
        "work_dir",
        "accelerator",
        "num_gpus",
        "stats_export_path",
        "use_actor",
        "auto_op_parallelism",
        "batch_mode",
        "runtime_np",
        "ray_execution_mode",
        "image_bytes_key",
        "video_frame_key",
        "save_dir",
    }
)
KINDS = {"Filter": "filter", "Mapper": "mapper", "Deduplicator": "deduplicator", "Selector": "selector"}
MULTIMODAL = ("image", "video", "audio", "mm_", "imgdiff", "frame")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _strip_null(schema: dict[str, Any]) -> dict[str, Any]:
    options = schema.get("anyOf")
    if isinstance(options, list):
        real = [o for o in options if o != {"type": "null"}]
        if len(real) == 1:
            merged = {k: v for k, v in schema.items() if k != "anyOf"}
            merged.update(real[0])
            return merged
    return schema


def derive(cls: type) -> tuple[dict[str, Any] | None, str | None]:
    """The parameter schema of ``cls`` within the subset, or None and the reason it is not."""
    from pydantic import TypeAdapter

    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(cls.__init__).parameters.items():
        if name in PLUMBING or param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.annotation is inspect.Parameter.empty:
            return None, f"parameter {name!r} has no type annotation"
        try:
            schema = _strip_null(TypeAdapter(param.annotation).json_schema())
        except Exception as exc:  # noqa: BLE001 - recorded as the rejection reason
            return None, f"parameter {name!r}: {type(exc).__name__}"
        schema.pop("title", None)
        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default is not None:
            try:
                canonical(param.default)
                if isinstance(param.default, int | float | str | bool | list):
                    schema["default"] = param.default
            except (TypeError, ValueError):
                pass
        properties[name] = schema
    full = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    violations = schema_subset.check(full)
    if violations:
        return None, "schema uses " + schema_subset.describe(violations[:3])
    return full, None


def _kind(cls: type) -> str | None:
    for base in inspect.getmro(cls):
        if base.__name__ in KINDS:
            return KINDS[base.__name__]
    return None


def build(version: str | None = None) -> dict[str, Any]:
    import yaml
    from data_juicer import __version__
    from data_juicer.ops.base_op import OPERATORS

    allow = yaml.safe_load(ALLOWLIST.read_text())["operators"]
    offered, rejected = [], []
    for op_name in sorted(OPERATORS.modules):
        cls = OPERATORS.modules[op_name]
        kind = _kind(cls)
        reason = None
        schema = None
        if any(m in op_name for m in MULTIMODAL):
            reason = "multimodal operators are excluded (BRD-03 section 4)"
        elif kind is None:
            reason = "not a filter, mapper, deduplicator or selector"
        else:
            schema, reason = derive(cls)
        entry = allow.get(op_name)
        if reason is None and entry is None:
            reason = "schema-able but not reviewed into allowlist.yaml"
        if reason is not None:
            rejected.append({"op_name": op_name, "reason": reason})
            continue
        assert schema is not None and kind is not None and entry is not None
        for param, comparator in (entry.get("threshold_params") or {}).items():
            schema["properties"][param]["x-unit"] = entry.get("unit", "")
        name = f"dj_{op_name}"
        thresholds = [
            {
                "param": t["param"],
                "statistic": entry["stats_key"],
                "unit": entry.get("unit", ""),
                "drop_when": t["drop_when"],
                "pair_param": t.get("pair_param"),
            }
            for t in entry.get("thresholds", [])
        ]
        manifest = {
            "name": name,
            "version": f"dj{version or __version__}-1",
            "provider": "datajuicer",
            "provider_version": version or __version__,
            "kind": kind,
            "scope": "dataset" if kind in {"deduplicator", "selector"} else "row",
            "description": entry["description"],
            "input_columns": [{"name": "text", "type": "string", "required": True}],
            "output_columns": [],
            "params_schema": schema,
            "thresholds": thresholds,
            "resources": {"queue": "datajuicer", "cpu_class": "medium", "memory_class": "medium"},
            "deterministic": True,
        }
        offered.append(
            {
                "op_name": op_name,
                "stats_key": entry.get("stats_key"),
                "threshold_params": entry.get("threshold_params") or {},
                "manifest": manifest,
            }
        )
    for item in allow:
        if item not in {o["op_name"] for o in offered}:
            reasons = [r["reason"] for r in rejected if r["op_name"] == item]
            raise SystemExit(f"allowlist.yaml names {item!r}, which cannot be offered: {reasons}")
    return {
        "format": "midataworks.datajuicer-catalogue/v1",
        "provider": "datajuicer",
        "provider_version": version or __version__,
        "total_ops": len(OPERATORS.modules),
        "operators": offered,
        "rejected": rejected,
    }


def render(document: dict[str, Any]) -> bytes:
    return canonical(document) + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = render(build())
    if args.check:
        current = OUTPUT.read_bytes() if OUTPUT.exists() else b""
        if current != data:
            print(f"{OUTPUT} is out of date; run build_catalogue.py", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_bytes(data)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
