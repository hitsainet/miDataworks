"""The native Threshold labeler (005 FTASKS 12.1; FR-005.23)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pyarrow as pa
import pytest

from src.operators.context import RunContext
from src.operators.errors import OperatorError
from src.operators.manifest import manifest_hash
from src.operators.native.threshold_labeler import MANIFEST, ThresholdLabeler
from tests.integration.labeling.helpers import setup_classifier, start_body, texts
from tests.support.labeling_fixtures import Labeling, row_key


def ctx() -> RunContext:
    return RunContext(
        MANIFEST, manifest_hash(MANIFEST), 1, None, {"text": "content"}, "dw.rowkey/v1"
    )


async def test_it_labels_from_a_completed_run_and_drops_the_band(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=40)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    labeling.run_until_done(run["id"])
    labels = (await client.get(f"/api/v1/label-runs/{run['id']}/labels?limit=100")).json()["items"]
    probs = {item["row_key"]: item["probability"] for item in labels}
    batch = pa.table(
        {
            "_dw_row_key": [row_key(t) for t in texts(40)],
            "_dw_occurrence": [0] * 40,
            "text": texts(40),
        }
    )
    params = {"label_run_id": run["id"], "threshold_positive": 0.5, "threshold_negative": 0.2}
    kept = ThresholdLabeler().run(batch, params, ctx())
    assert kept.output.num_rows == 40 and kept.events == []
    for key, label, p in zip(
        kept.output.column("_dw_row_key").to_pylist(),
        kept.output.column("label").to_pylist(),
        kept.output.column("label_probability").to_pylist(),
        strict=True,
    ):
        assert p == probs[key]
        assert label == ("positive" if p >= 0.5 else "negative" if p <= 0.2 else "excluded")
    dropped = ThresholdLabeler().run(batch, {**params, "drop_excluded": True}, ctx())
    excluded = sum(1 for p in probs.values() if 0.2 < p < 0.5)
    assert excluded > 0 and len(dropped.events) == excluded
    assert {e.reason_code for e in dropped.events} == {"excluded_by_band"}
    assert {e.statistic_name for e in dropped.events} == {"P"}
    assert dropped.output.num_rows == 40 - excluded


def test_an_unpublished_run_is_refused(data_dir: Path) -> None:
    batch = pa.table({"_dw_row_key": ["a" * 64], "_dw_occurrence": [0]})
    with pytest.raises(OperatorError) as exc:
        ThresholdLabeler().run(
            batch,
            {"label_run_id": "lr_nope", "threshold_positive": 0.5, "threshold_negative": 0.2},
            ctx(),
        )
    assert exc.value.code == "label_run_not_published"


def test_invalid_thresholds_are_refused(data_dir: Path) -> None:
    batch = pa.table({"_dw_row_key": ["a" * 64], "_dw_occurrence": [0]})
    with pytest.raises(OperatorError) as exc:
        ThresholdLabeler().run(
            batch,
            {"label_run_id": "x", "threshold_positive": 0.2, "threshold_negative": 0.5},
            ctx(),
        )
    assert exc.value.code == "thresholds_invalid"
