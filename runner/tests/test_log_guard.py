"""The runner process logs no text and no source (FR-018.25; FTASKS 6.9).

A real runner subprocess, stderr captured to a file: a run whose source and text carry sentinels,
a rule that raises on the text, a dry run with a diagnostic, an inspect, and refused requests. No
sentinel, nor any 12-character substring of one, may appear on stderr.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import httpx

from src.clients import runner_client as rc


def _windows(sentinel: str) -> set[str]:
    return {sentinel[i : i + 12] for i in range(len(sentinel) - 11)}


def test_the_runner_writes_no_text_or_source_to_stderr(
    bundle_dir: Path, runner_starter: Any, tmp_path: Path
) -> None:
    text_sentinel = "txt" + secrets.token_hex(16)
    source_sentinel = "src" + secrets.token_hex(16)
    document = {
        "runner_contract": "midataworks.runner/v1",
        "source": (
            f"# {source_sentinel}\n"
            "def f(text, params):\n"
            "    if 'boom' in text:\n"
            f"        raise ValueError('{source_sentinel}' + text)\n"
            "    return len(text.split())\n"
        ),
        "functions": [{"name": "f", "output": {"type": "count"}}],
        "params_schema": {"type": "object"},
        "derived_from": {"sources": [], "statement": "log guard"},
    }
    contract = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "backend/src/schemas/data/midataworks-runner-v1.json"
        ).read_text(encoding="utf-8")
    )
    floor = contract["limits_floor"]
    limits = {k: floor[k] for k in contract["request_limits"] if k in floor}
    limits["wall_ms"] = 10_000
    stderr = tmp_path / "runner.stderr"
    with runner_starter(bundle_dir, stderr_path=stderr) as runner:
        client = rc.RunnerClient(runner.base_url)
        ref = rc.DocumentRef.of(document)
        texts = [f"a {text_sentinel}", f"boom {text_sentinel}"]
        for mode in ("stored", "dry_run", "save_check"):
            answer = client.run(
                ref,
                [rc.RunCall("f", {}, texts)],
                mode=mode,
                lane="batch",
                limits=limits,
                send_document=True,
            )
            assert answer.results[0][0] == {"count": 2} or "count" in answer.results[0][0]
            assert answer.results[0][1]["reason"] == "rule_error"
        assert client.inspect(ref) == {"functions": ["f"], "imports": []}
        # Refused requests: a hash that does not match, and a limit below the floor.
        bad = rc.run_body(
            ref,
            [rc.RunCall("f", {}, texts)],
            mode="stored",
            lane="batch",
            limits=limits,
            include_document=True,
        )
        bad["document"]["code_sha256"] = "0" * 64
        assert httpx.post(runner.base_url + "/v1/run", json=bad, timeout=30).status_code == 422
        low = dict(bad, limits={**limits, "call_fuel": 1})
        assert httpx.post(runner.base_url + "/v1/run", json=low, timeout=30).status_code == 422
    written = stderr.read_text(encoding="utf-8", errors="replace")
    leaked = sorted(
        w for s in (text_sentinel, source_sentinel) for w in _windows(s) if w in written
    )
    assert leaked == [], f"{len(leaked)} sentinel fragments on the runner's stderr"
