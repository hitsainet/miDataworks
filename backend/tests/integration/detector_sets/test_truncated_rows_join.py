"""The paired-score source through ``join`` and the refresh, with miWorkbench's G5 shape (021
FTASKS 20.3; FR-021.33; P44, P66). Real send fixtures, the fake miWorkbench serving per-row
``truncated`` flags and ``reported_auroc_untruncated``. The untruncated AUROC is computed here by
sklearn over the rows the test keeps, independently of the filter under test (R2B-5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sklearn.metrics import roc_auc_score

from tests.integration.detector_sets.test_results_and_marks import sent_set, serve_run_c
from tests.integration.detector_sets.test_results_provenance import (
    OOD_EVAL,
    PROBE,
    millm_calls,
    ood_entry,
    ood_of,
    refresh,
    scores_for,
)
from tests.support.send_fixtures import SendDriver

__all__ = ["millm_calls"]


def flagged(served: dict[str, Any]) -> tuple[dict[str, Any], float]:
    """Flag two rows truncated and turn their scores against their labels, so the all-rows AUROC
    and the untruncated one differ; return the served answer and the untruncated AUROC."""
    rows = served["rows"]
    for i, row in enumerate(rows):
        row["truncated"] = i < 2
        if i < 2:
            row["score"] = 5.0 if row["label"] == 0 else -5.0
    kept = rows[2:]
    untruncated = float(roc_auc_score([r["label"] for r in kept], [r["score"] for r in kept]))
    served["reported_auroc"] = float(
        roc_auc_score([r["label"] for r in rows], [r["score"] for r in rows])
    )
    assert abs(served["reported_auroc"] - untruncated) > 5e-4
    return served, untruncated


async def test_flagged_rows_are_dropped_and_the_source_accepted(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
) -> None:
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    served, untruncated = flagged(scores_for(ood_of(send)))
    served["reported_auroc_untruncated"] = untruncated
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = served
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["available"] is True and paired["source"] == "mistudio", paired
    assert paired["excluded_truncated"] == 2
    assert "2 row(s) miWorkbench truncated were left out" in paired["acceptance"]
    assert millm_calls == []


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing", "untruncated_auroc_missing"),
        ("null", "too_few_untruncated"),
        ("null_with_reason", "truncation_not_recorded"),
    ],
)
async def test_a_flagged_answer_without_its_figure_is_refused_and_falls_back(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    millm_calls: list[str],
    case: str,
    reason: str,
) -> None:
    """Behavioural half of ``test_join_calls_select_served_rows``: through the real ``join``, a
    flagged answer with the key dropped (or null) is refused by name, never checked against the
    all-rows AUROC, and the miServer source is consulted."""
    sender.mistudio.serves_scores = True
    set_id, send = await sent_set(client, sender)
    serve_run_c(sender, send)
    served, _ = flagged(scores_for(ood_of(send)))
    if case.startswith("null"):
        served["reported_auroc_untruncated"] = None
    if case == "null_with_reason":
        # miWorkbench G5 names why it states no figure; the refusal carries that reason
        served["untruncated_reason"] = reason
    sender.mistudio.scores[(PROBE, OOD_EVAL)] = served
    paired = ood_entry(await refresh(client, set_id))["paired"]
    assert paired["source"] != "mistudio"
    skipped = [s["reason"] for s in paired["skipped"]]
    assert any(r.startswith(reason) for r in skipped), skipped
    assert millm_calls  # fell through to the miServer source
