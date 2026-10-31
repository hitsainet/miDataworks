"""The ``generated_held_out`` caveat and the proof on each published split (017 FR-017.5, FR-017.2).

A version whose value-directed held-out split holds generated rows under a passing disjointness
proof (ENH-006 D10) says so in its published manifest: one caveat, severity ``note``, naming each
such split, its directing columns, its held-out values and the proof's counts. The proof itself
rides ``Split.extensions.held_out_proof`` (``extensions`` is open in
``midataworks.dataset-version/v1``, so no schema change; ``docs/schemas/README.md``).

One helper per fact, called at EVERY place a dataset-version manifest is assembled (FTID 017 I4,
X-6): ``publish_job.run_publish`` and ``drafts.built_document``, and (Review R1 F3) the two export
manifests ``export_service.export_manifest`` (TRL, miForge set) and ``reward_bundle._manifest``,
as 015's ``session_data_caveat`` / ``with_session_caveat``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..curation.held_out_proof import CITATION

CODE = "generated_held_out"


def _directed(splits: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        s
        for s in splits
        if s.get("held_out") and isinstance(s.get("proof"), Mapping) and s["proof"].get("passes")
    ]


def held_out_caveat(splits: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The caveat, or None when no held-out split holds generated rows under a proof."""
    named = [s for s in _directed(splits) if int(s["proof"].get("generated_rows") or 0) > 0]
    if not named:
        return None
    detail = [
        {
            "split": s["name"],
            "columns": list(s["proof"]["columns"]),
            "held_out_values": dict(s["proof"]["held_out_values"]),
            "rows": int(s["proof"]["rows"]),
            "generated_rows": int(s["proof"]["generated_rows"]),
            "training_values": dict(s["proof"]["training_values"]),
            "shared_groups": dict(s["proof"].get("shared_groups") or {}),
            # D42: training values equal to a held-out value but for case or whitespace
            "near_values": [dict(v) for v in s["proof"].get("near_values") or []],
            "near_values_count": int(s["proof"].get("near_values_count") or 0),
        }
        for s in named
    ]
    names = ", ".join(repr(d["split"]) for d in detail)
    generated = sum(d["generated_rows"] for d in detail)
    near = sum(d["near_values_count"] for d in detail)
    near_note = (
        f" {near} training value(s) differ from a held-out value only in case or whitespace "
        "(detail.splits[].near_values): the values are distinct as written, not as read."
        if near
        else ""
    )
    return {
        "code": CODE,
        "severity": "note",
        "message": f"The held-out split(s) {names} hold {generated:,} generated row(s), placed "
        "there by declared held-out values that the build proved absent from training "
        f"({CITATION}).{near_note}",
        "detail": {"splits": detail, "rule": CITATION},
    }


def split_extensions(splits: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Split name -> the published split's ``extensions`` (the proof of a directed split)."""
    return {
        str(s["name"]): {"held_out_proof": dict(s["proof"])}
        for s in splits
        if s.get("held_out") and isinstance(s.get("proof"), Mapping)
    }


def with_held_out_caveat(
    caveats: Sequence[Mapping[str, Any]], splits: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """``caveats`` plus the ``generated_held_out`` caveat when the version has one (the export
    manifests; Review R1 F3 - 017 had reached only the draft and the publish)."""
    out = [dict(c) for c in caveats]
    note = held_out_caveat(splits)
    if note is not None:
        out.append(note)
    return out
