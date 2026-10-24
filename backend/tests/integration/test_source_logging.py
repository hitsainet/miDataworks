"""Import logging (001 FTASKS 12.6, 12.7; FTDD 11): one structured line per phase with ids,
phase and numeric counts; never a request body, a token, a file name or row text. A value logged
under a secret FIELD NAME is redacted even when nobody registered it."""

from __future__ import annotations

import logging
import re

import httpx
import pytest

from src.core.logging import REDACTED, install_redaction, redact
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
TOKEN = "hf_logging_token_never_seen_42"
PHASE = re.compile(r"^source_import job_id=(\S+) source_id=(\S+) phase=(\w+) (.*)duration_ms=\d+$")


@pytest.mark.parametrize(
    "line",
    [
        "request body access_token=hf_unregistered_123",
        '{"access_token": "hf_unregistered_123", "repo_id": "a/b"}',
        "headers {'Authorization': 'Bearer hf_unregistered_123'}",
        "hf_token: hf_unregistered_123",
    ],
)
def test_a_value_under_a_secret_field_name_is_redacted(
    line: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert "hf_unregistered_123" not in redact(line) and REDACTED in redact(line)
    install_redaction()  # done at start-up by main.create_app and workers.startup
    with caplog.at_level(logging.INFO):
        logging.getLogger("test.redaction").info("%s", line)
    assert "hf_unregistered_123" not in caplog.text


def test_ordinary_prose_is_left_alone() -> None:
    text = "access tokens are read from Settings; api keys too"
    assert redact(text) == text


async def test_each_phase_logs_one_structured_line_and_nothing_else(
    client: httpx.AsyncClient,
    hf_env: HfEnv,
    operator_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO):
        response = await client.post(
            "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": TOKEN}
        )
        assert response.status_code == 202, response.text
        [result] = hf_env.run_imports()
    job_id = response.json()["job_id"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("source_import ")]
    phases = [PHASE.match(line) for line in lines]
    assert all(phases), lines
    names = [m.group(3) for m in phases if m]
    assert names[0] == "resolve" and names[-1] == "completed"
    assert {"download", "commit"} <= set(names)
    assert len(names) == len(set(names)), "one line per phase, not per heartbeat"
    assert all(m.group(1) == job_id for m in phases if m)
    commit = next(m for m in phases if m and m.group(3) == "commit")
    assert commit.group(2) == result["source_id"] and "files=3" in commit.group(4)
    every = caplog.text
    assert TOKEN not in every
    for row_text in ("joke number", "walks into a bar", "one-liner", "headline about rates"):
        assert row_text not in every
    assert '"repo_id"' not in every and "access_token" not in every


def test_log_phase_writes_numbers_only_and_one_line_per_phase(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The guarantee is in the helper, so it holds for every caller, including fields a future
    caller passes (a split name, a file name, a row)."""
    from src.workers.source_tasks import log_phase

    with caplog.at_level(logging.INFO):
        log_phase("job_x", "download", split="private-split-name", bytes=10, rows=3)
        log_phase("job_x", "download", bytes=20)  # a heartbeat in the same phase
        log_phase("job_x", "write", original_name="secret.csv", text="a row", files=2)
        log_phase("job_x", "completed", "src-1")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("source_import ")]
    assert [PHASE.match(line).group(3) for line in lines] == ["download", "write", "completed"]  # type: ignore[union-attr]
    assert "bytes=10 rows=3" in lines[0] and "files=2" in lines[1]
    for leaked in ("private-split-name", "secret.csv", "a row"):
        assert leaked not in caplog.text
    assert "source_id=src-1" in lines[2]
