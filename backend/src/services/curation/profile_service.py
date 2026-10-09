"""The profile: counts, nulls, lengths, turns, duplicates, clusters, contamination and the audit,
each with its scale, its sample size and an honest status (FR-004.6–004.8).

Every figure is ``{"status": "computed", "n_rows", "sample", ...}`` or
``{"status": "not_computed", "reason", "action"}``. A figure that cannot be computed is NEVER zero
and never silently computed another way (FR-004.8): language identification (no allowlisted
operator, no approved library), token lengths (no tokenizer in v1, T-15), embedding near
duplicates (after M1, P-18), contamination without chosen benchmarks, and the audit without a
label column.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
from sqlalchemy.orm import Session

from ...models.enums import ColumnRole
from . import cluster_service, label_columns, minhash, text_stats
from .codes import ReportInput, files_for, load_table, schema_names

HIST_BINS = 20
CLUSTER_K = 10


def not_computed(reason: str, action: str) -> dict[str, Any]:
    return {"status": "not_computed", "reason": reason, "action": action}


def computed(n_rows: int, sample: bool, **values: Any) -> dict[str, Any]:
    return {"status": "computed", "n_rows": n_rows, "sample": sample, **values}


def histogram(values: list[float], unit: str) -> dict[str, Any]:
    if not values:
        return {"unit": unit, "edges": [], "counts": []}
    counts, edges = np.histogram(np.asarray(values, dtype=float), bins=HIST_BINS)
    return {
        "unit": unit,
        "edges": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
        "median": float(np.median(values)),
    }


def _counts(table: pa.Table, column: str) -> dict[str, int]:
    if column not in table.schema.names:
        return {}
    out: dict[str, int] = {}
    for v in table.column(column).to_pylist():
        key = "(empty)" if v is None else str(v)
        out[key] = out.get(key, 0) + 1
    return out


def build_profile(
    table: pa.Table, roles: dict[str, str], *, sample: bool, seed: int
) -> dict[str, Any]:
    n = table.num_rows
    content = sorted(
        c for c, r in roles.items() if r == ColumnRole.CONTENT and c in table.schema.names
    )
    figures: dict[str, Any] = {}
    figures["counts"] = computed(
        n,
        sample,
        by_split=_counts(table, "_dw_split"),
        by_source=_counts(table, "_dw_source_id"),
        by_origin=_counts(table, "_dw_origin"),
    )
    nulls = {}
    for name in table.schema.names:
        if name.startswith("_dw_"):
            continue
        values = table.column(name).to_pylist()
        nulls[name] = {
            "nulls": sum(v is None for v in values),
            "empty_strings": sum(isinstance(v, str) and not v.strip() for v in values),
        }
    figures["nulls"] = computed(n, sample, columns=nulls)
    lengths = {}
    turns: dict[str, Any] = {}
    for column in content:
        values = table.column(column).to_pylist()
        lengths[column] = {
            "characters": histogram(
                [float(text_stats.char_length(v)) for v in values], "characters"
            ),
            "words": histogram([float(text_stats.word_length(v)) for v in values], "words"),
        }
        chat = [v for v in values if isinstance(v, list)]
        if chat:
            by_role: dict[str, int] = {}
            for v in chat:
                for role, c in text_stats.turns_by_role(v).items():
                    by_role[role] = by_role.get(role, 0) + c
            turns[column] = {
                "turns": histogram([float(len(v)) for v in chat], "turns"),
                "by_role": by_role,
                "chat_rows": len(chat),
            }
    figures["lengths"] = computed(n, sample, columns=lengths)
    figures["tokens"] = not_computed(
        "No tokenizer is loaded in version 1 (T-15), so lengths are in characters and words.",
        "Nothing to configure in v1.",
    )
    figures["turns"] = (
        computed(n, sample, columns=turns)
        if turns
        else not_computed("No content column holds chat messages.", "None needed for plain text.")
    )
    figures["language"] = not_computed(
        "No language-identification operator is allowlisted (feature 003's Data-Juicer catalogue "
        "has none) and no approved library provides one.",
        "Allowlist a language-identification operator on the Operators screen when one exists.",
    )
    keys = table.column("_dw_row_key").to_pylist() if "_dw_row_key" in table.schema.names else []
    groups: dict[str, int] = {}
    for k in keys:
        groups[str(k)] = groups.get(str(k), 0) + 1
    dup = [c for c in groups.values() if c > 1]
    figures["exact_duplicates"] = computed(n, sample, groups=len(dup), rows=sum(dup) - len(dup))
    texts = cluster_service.texts_of(table, content)
    if texts:
        sigs = minhash.signatures_for(texts, kind="word", size=5, count=128, seed=seed)
        near, _ = minhash.near_duplicate_groups(sigs, minhash.band_layout(128, 0.8), 0.8)
        figures["near_duplicates"] = computed(
            n,
            sample,
            basis="lexical",
            threshold=0.8,
            groups=len(near),
            rows=sum(len(g) - 1 for g in near.values()),
        )
    else:
        figures["near_duplicates"] = not_computed("The version has no content to compare.", "")
    figures["near_duplicates_embedding"] = not_computed(
        "Embedding-based near duplicates are built after M1 (P-18); the lexical figure above is the "
        "M1 basis.",
        "Configure the embeddings role in Settings once embedding operators ship.",
    )
    if texts and n >= 2:
        fitted = cluster_service.fit(texts, min(CLUSTER_K, max(1, n // 2)), seed)
        summary = cluster_service.summary(table, texts, fitted)
        summary.pop("n_rows", None)
        figures["clusters"] = computed(n, sample, **summary)
    else:
        figures["clusters"] = not_computed("Too few rows to cluster.", "")
    figures["contamination"] = not_computed(
        "No benchmark was chosen for this profile.",
        "Run the contamination check with benchmarks from the catalogue.",
    )
    return {"n_rows": n, "sample": sample, "figures": figures}


def compute_profile(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    from .api import audit_params, require_version
    from .audit_service import AuditRefusal, run_audit
    from .report_service import Computed

    version = require_version(session, inputs[0].version_id)
    source = files_for(version, inputs[0])
    names = list(schema_names([source]))
    table = load_table([source], names)
    raw_size = params.get("sample_size")
    size = int(raw_size) if raw_size else 0
    sample = bool(size) and table.num_rows > size
    if sample:
        rng = np.random.default_rng(seed)
        keep = np.sort(rng.choice(table.num_rows, size=size, replace=False))
        table = table.take(pa.array(keep))
    profile = build_profile(table, dict(version.column_roles), sample=sample, seed=seed)
    labels = label_columns.resolve(session, version.id)
    if labels.label is None:
        profile["figures"]["shortcut_audit"] = not_computed(
            "No label column: no labeling step wrote one.",
            "Add a labeling step, or run the shortcut audit with a chosen label column.",
        )
    else:
        try:
            audit, _ = run_audit(session, [inputs[0]], audit_params(labels.label), seed)
            profile["figures"]["shortcut_audit"] = computed(audit["n_rows"], False, audit=audit)
        except AuditRefusal as exc:
            profile["figures"]["shortcut_audit"] = not_computed(exc.message, "")
    return Computed(result=profile, artefacts=[], rows=table.num_rows)
