"""No refusal, detail or log line written by 021 code carries row text (021 FTASKS 18.3; FTID §12).

A held-out row's text in a log or a refusal would be a second leak path. Each WSP refusal below is
driven by rows carrying a sentinel string; the job's error envelope and every captured log record
must name splits, counts and row keys only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest

from tests.integration.test_directed_held_out import ECHO, driver
from tests.integration.test_wsp_versions import ALPHA_SPLIT, alpha_rows, build
from tests.support.operator_build_driver import RealDriver

__all__ = ["driver"]

SENTINEL = "ZQX-sentinel-held-out-text-7731"
#: the long partition's floor raised above every fixture row, so the sentinel row is among the short
HIGH_FLOOR = json.loads(json.dumps(ALPHA_SPLIT))
HIGH_FLOOR["params"]["wsp_min_chars"] = [0, 0, 0, 500]


def with_sentinel(case: str) -> dict[str, list[dict[str, str]]]:
    rows = alpha_rows(f"nt-{case}")
    if case == "floor":
        rows["long"].append({"text": f"{SENTINEL} short", "label": "pos"})
    elif case == "shares":
        row = {"text": f"{SENTINEL} long " + "word " * 30, "label": "pos"}
        rows["long"].append(row)
        rows["train"].append(dict(row))
    else:  # generated rows reach a held-out partition
        rows["everyday"].append({"text": f"{SENTINEL} everyday", "label": "neg"})
    return rows


@pytest.mark.parametrize(
    ("case", "code", "steps"),
    [
        ("floor", "wsp_long_rows_below_floor", [HIGH_FLOOR]),
        ("shares", "wsp_heldout_shares_rows", [ALPHA_SPLIT]),
        ("generated", "wsp_heldout_contains_generated", [ALPHA_SPLIT, ECHO]),
    ],
)
async def test_no_refusal_or_log_carries_row_text(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    caplog: pytest.LogCaptureFixture,
    case: str,
    code: str,
    steps: list[dict[str, object]],
) -> None:
    caplog.set_level(logging.DEBUG)
    out = await build(client, driver, data_dir, with_sentinel(case), steps, f"nt-{case}")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == code
    assert SENTINEL not in json.dumps(out["error"])
    assert all(SENTINEL not in record.getMessage() for record in caplog.records)
    assert SENTINEL not in caplog.text
