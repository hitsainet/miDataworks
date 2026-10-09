"""TRL shape and target-type detection (FR-001.19, FR-001.20; 001 FTDD section 6.5).

``detect(columns, sample_rows) -> Detection``. The rules are evaluated ALL, in order; one full match
is the result, and zero or several full matches give ``undetected`` with every rule's reason. Each
output carries a one-sentence reason naming the columns, so the operator sees why. The text column
is never "the first column by default" (mutation control M9): a column is text-like only when it is
a string column whose sample median length is at least 8 characters.

The same function runs on preview samples and on the stored Parquet after import (the first 1,000
rows of the largest split); the stored result is authoritative. A ``from``/``value`` (ShareGPT)
column is reported, never converted — converting is an operator's job.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DETECTOR_VERSION = "dw.detect/v1"
TRL_NAMES = {
    "prompt",
    "completion",
    "completions",
    "chosen",
    "rejected",
    "messages",
    "label",
    "labels",
    "text",
}
SAMPLE_ROWS = 1000
#: The vocabularies ``detect`` answers in; ``/sources/meta`` serves these, so no list drifts.
TRL_TYPES = (
    "language_modeling",
    "prompt_only",
    "prompt_completion",
    "preference",
    "unpaired_preference",
    "stepwise_supervision",
    "none",
    "undetected",
)
TRL_FORMATS = ("standard", "conversational")
CHAT_FORMATS = ("plain_text", "role_content", "from_value", "unknown")


@dataclass
class Detection:
    detector_version: str
    trl_type: str
    trl_format: str | None
    chat_format: str
    text_columns: list[str]
    label_columns: list[str]
    suggested_target: str
    reasons: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "detector_version": self.detector_version,
            "trl_type": self.trl_type,
            "trl_format": self.trl_format,
            "chat_format": self.chat_format,
            "text_columns": self.text_columns,
            "label_columns": self.label_columns,
            "suggested_target": self.suggested_target,
            "reasons": self.reasons,
        }


def _values(rows: Sequence[dict[str, Any]], column: str) -> list[Any]:
    return [r.get(column) for r in rows if r.get(column) is not None]


def _is_role_content(values: list[Any]) -> bool:
    return bool(values) and all(
        isinstance(v, list)
        and v
        and all(isinstance(m, dict) and "role" in m and "content" in m for m in v)
        for v in values[:20]
    )


def _is_from_value(values: list[Any]) -> bool:
    return bool(values) and all(
        isinstance(v, list)
        and v
        and all(isinstance(m, dict) and "from" in m and "value" in m for m in v)
        for v in values[:20]
    )


def text_like(type_name: str, values: list[Any]) -> bool:
    strings = [v for v in values if isinstance(v, str)]
    if not strings or ("string" not in type_name.lower() and "str" not in type_name.lower()):
        return False
    return statistics.median(len(s) for s in strings) >= 8


def label_like(type_name: str, values: list[Any]) -> bool:
    lowered = type_name.lower()
    if "bool" in lowered:
        return True
    if any(t in lowered for t in ("int", "string", "str")) and values:
        return len({str(v) for v in values}) <= 20 and not text_like(type_name, values)
    return False


def detect(columns: Sequence[tuple[str, str]], sample_rows: Sequence[dict[str, Any]]) -> Detection:
    """``columns`` are ``(name, Arrow or HF type)``; ``sample_rows`` at most ``SAMPLE_ROWS``."""
    names = {n for n, _ in columns}
    types = dict(columns)
    rows = list(sample_rows[:SAMPLE_ROWS])
    vals = {n: _values(rows, n) for n in names}
    chat_cols = [n for n in names if _is_role_content(vals[n])]
    sharegpt = [n for n in names if _is_from_value(vals[n])]
    texts = sorted(n for n in names if text_like(types[n], vals[n]))
    labels = sorted(n for n in names if label_like(types[n], vals[n]) and n not in texts)
    trl_present = names & TRL_NAMES

    rules: list[tuple[str, bool, str, str | None, str, str]] = [
        (
            "preference",
            {"chosen", "rejected"} <= names,
            "preference",
            "standard",
            "dpo",
            "Columns chosen and rejected make a preference dataset.",
        ),
        (
            "unpaired_preference",
            {"prompt", "completion", "label"} <= names
            and "bool" in str(types.get("label", "")).lower(),
            "unpaired_preference",
            "standard",
            "kto",
            "Columns prompt, completion and a boolean label make an unpaired preference dataset.",
        ),
        (
            "stepwise",
            {"prompt", "completions", "labels"} <= names,
            "stepwise_supervision",
            "standard",
            "prm",
            "Columns prompt, completions and labels make stepwise supervision.",
        ),
        (
            "prompt_completion",
            {"prompt", "completion"} <= names and "label" not in names and "chosen" not in names,
            "prompt_completion",
            "standard",
            "sft",
            "Columns prompt and completion make a prompt-completion dataset.",
        ),
        (
            "messages",
            "messages" in chat_cols,
            "language_modeling",
            "conversational",
            "sft",
            "Column messages holds role/content turns: a conversational dataset.",
        ),
        (
            "prompt_only",
            trl_present == {"prompt"},
            "prompt_only",
            "standard",
            "grpo_prompt",
            "Column prompt is the only TRL column: a prompt-only dataset.",
        ),
        (
            "text_only",
            trl_present == {"text"} and not labels,
            "language_modeling",
            "standard",
            "sft",
            "Column text is the only TRL column and no column looks like a label.",
        ),
        (
            "detector",
            not trl_present - {"text", "label", "labels"}
            and len(texts) == 1
            and len(labels) >= 1
            and not {"chosen", "rejected", "prompt", "completion"} & names,
            "none",
            None,
            "detector",
            f"One text-like column ({texts[0] if texts else '-'}) and a label-like column "
            f"({labels[0] if labels else '-'}): a detector set.",
        ),
    ]
    matches = [r for r in rules if r[1]]
    reasons: list[dict[str, str]] = []
    if len(matches) == 1:
        _, _, trl_type, trl_format, target, reason = matches[0]
        reasons.append({"output": "trl_type", "reason": reason})
    else:
        trl_type, trl_format, target = "undetected", None, "untyped"
        if matches:
            reasons.extend({"output": "trl_type", "reason": f"Ambiguous: {m[5]}"} for m in matches)
        else:
            reasons.append(
                {"output": "trl_type", "reason": f"No rule matched the columns {sorted(names)}."}
            )
    if chat_cols:
        chat_format = "role_content"
        reasons.append(
            {"output": "chat_format", "reason": f"Column {chat_cols[0]} holds role/content turns."}
        )
    elif sharegpt:
        chat_format = "from_value"
        reasons.append(
            {
                "output": "chat_format",
                "reason": f"Column {sharegpt[0]} holds from/value turns (ShareGPT); it is reported, not converted.",
            }
        )
    elif any("string" in str(t).lower() for t in types.values()):
        chat_format = "plain_text"
        reasons.append({"output": "chat_format", "reason": "Only plain string columns hold text."})
    else:
        chat_format = "unknown"
        reasons.append({"output": "chat_format", "reason": "No column holds text."})
    if texts:
        reasons.append(
            {
                "output": "text_columns",
                "reason": f"{texts} are strings with a median length of 8 or more characters.",
            }
        )
    if labels:
        reasons.append(
            {
                "output": "label_columns",
                "reason": f"{labels} are boolean, or have at most 20 distinct values in the sample.",
            }
        )
    return Detection(
        detector_version=DETECTOR_VERSION,
        trl_type=trl_type,
        trl_format=trl_format,
        chat_format=chat_format,
        text_columns=texts,
        label_columns=labels,
        suggested_target=target,
        reasons=reasons,
    )


def detect_stored(files: Sequence[tuple[Path, int]]) -> dict[str, Any] | None:
    """Detection on the stored Parquet (001 FTASKS 5.3): the first ``SAMPLE_ROWS`` rows of the
    largest split, through the same :func:`detect` the preview uses. ``files`` are
    ``(path, rows)``; the stored result is authoritative."""
    if not files:
        return None
    import pyarrow.parquet as pq

    path, _ = max(files, key=lambda f: f[1])
    handle = pq.ParquetFile(path)
    rows: list[dict[str, Any]] = []
    for batch in handle.iter_batches(batch_size=SAMPLE_ROWS):
        rows.extend(batch.to_pylist())
        if len(rows) >= SAMPLE_ROWS:
            break
    columns = [(f.name, str(f.type)) for f in handle.schema_arrow]
    return detect(columns, rows[:SAMPLE_ROWS]).as_dict()


__all__ = [
    "CHAT_FORMATS",
    "TRL_FORMATS",
    "TRL_TYPES",
    "DETECTOR_VERSION",
    "Detection",
    "detect",
    "detect_stored",
    "label_like",
    "text_like",
]
