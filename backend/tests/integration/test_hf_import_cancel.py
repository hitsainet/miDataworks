"""Cancel mid-download (001 FTASKS 7.8; FR-001.9).

A slow fake loader (short Python steps, writing into its cache) is interrupted by the operator's
cancel through ``CancelWatchdog``; the job and the source end ``cancelled``; this job's
``runs/<job>/hf_cache/`` and ``staging/<job>/`` are removed; another job's cache is untouched.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import httpx

from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env, sources_where

__all__ = ["hf_env"]


async def test_cancel_mid_download_stops_and_cleans_only_this_job(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, operator_name: str
) -> None:
    neighbour = data_dir / "runs" / "job_neighbour" / "hf_cache"
    neighbour.mkdir(parents=True)
    (neighbour / "keep.bin").write_bytes(b"theirs")
    hf_env.loader.slow = True
    response = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT})
    job_id = response.json()["job_id"]
    worker = threading.Thread(target=hf_env.run_imports, daemon=True)
    worker.start()
    cache = data_dir / "runs" / job_id / "hf_cache"
    deadline = time.monotonic() + 10
    while not (cache.exists() and any(cache.iterdir())) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert cache.exists(), "the download never started"
    started = time.monotonic()
    cancel = await client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "wrong dataset"})
    assert cancel.status_code == 202, cancel.text
    await asyncio.to_thread(worker.join, 20)
    assert not worker.is_alive(), "the watchdog did not interrupt the download"
    assert time.monotonic() - started < 10
    assert hf_env.job(job_id).status == "cancelled"
    [source] = sources_where(import_job_id=job_id)
    assert source.state == "cancelled" and source.error["code"] == "cancelled"
    assert not cache.exists()
    assert not (data_dir / "staging" / job_id).exists()
    assert not (data_dir / "sources" / source.id).exists()
    assert (neighbour / "keep.bin").read_bytes() == b"theirs"
    assert "source_import:cancelled" in [e for _, e, _ in hf_env.emitted]
