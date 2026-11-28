"""The reward bundle (008 FTASKS 13.1 - 13.5; FR-008.46 - FR-008.49; suite tracker 4.6).

The bundle is exported through the real export job; its ``reward.py`` is then imported from the
written files and called exactly as TRL 1.14.1's GRPO trainer calls a reward function, against a
local fake judge (a real HTTP server in a thread, so the retry and parsing paths run for real).
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from src.core.storage import export_dir
from src.services.publishing.manifest_builder import MANIFEST_FILE, validate_against_file
from tests.integration.publishing.test_trl_export import version_of
from tests.support.publish_fixtures import PublishDriver, publisher
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

BINARY = {
    "style": "binary",
    "messages": [
        {
            "role": "user",
            "content": "Prompt: {prompt}\nAnswer: {completion}\nIs it a pun? VERDICT: yes or no.",
        }
    ],
    "input_fields": ["prompt", "completion"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}
SCALE = {
    **BINARY,
    "style": "pointwise",
    "allowed_verdicts": ["1", "2", "3", "4", "5"],
}


async def rubric(client: httpx.AsyncClient, name: str, body: dict[str, Any]) -> str:
    r = await client.post("/api/v1/rubrics", json={"name": name, "body": body})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def export(client: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
    return await client.post("/api/v1/exports", json={"target": "reward_bundle", **payload})


@pytest.fixture
async def bundle(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> dict[str, Any]:
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    rubric_id = await rubric(client, "pun/judge", BINARY)
    r = await export(
        client,
        {
            "version_id": version_id,
            "rubric_id": rubric_id,
            "judge_model_id": "judge-7b",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
            "seed": 11,
        },
    )
    assert r.status_code == 202, r.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{r.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    return {"row": row, "dir": export_dir(row["id"]), "version_id": version_id}


async def test_the_bundle_holds_its_four_artifacts_and_a_valid_manifest(
    bundle: dict[str, Any],
) -> None:
    files = {f["path"]: f["role"] for f in bundle["row"]["files"]}
    assert files == {
        "reward.py": "reward_function",
        "rubric.json": "rubric",
        "config.json": "config",
        "README.md": "readme",
    }
    path = bundle["dir"] / MANIFEST_FILE
    validate_against_file(json.loads(path.read_bytes()))
    manifest = json.loads(path.read_bytes())
    assert manifest["target"]["kind"] == "reward_bundle"
    content = manifest["content"]
    assert content["content_kind"] == "reward_bundle" and content["judge_model_id"] == "judge-7b"
    assert content["rubric"]["style"] == "binary" and content["trl_version"] == "1.14.1"
    assert manifest["version"]["version_id"] == bundle["version_id"]
    assert {a["path"] for a in content["artifacts"]} == set(files)


async def test_no_url_key_or_secret_is_written_and_the_readme_says_it_is_not_an_evaluation(
    bundle: dict[str, Any],
) -> None:
    for name in ("reward.py", "rubric.json", "config.json", "README.md", MANIFEST_FILE):
        text = (bundle["dir"] / name).read_text(encoding="utf-8")
        assert not re.search(r"https?://", text), name
        assert "test-only" not in text and "hf_" not in text, name
    config = json.loads((bundle["dir"] / "config.json").read_bytes())
    assert config["base_url_env"] == "MIDATAWORKS_JUDGE_BASE_URL"
    assert config["api_key_env"] == "MIDATAWORKS_JUDGE_API_KEY"
    readme = (bundle["dir"] / "README.md").read_text()
    assert "A reward score is not an evaluation result" in readme
    assert "separate held-out detector or judge" in readme


async def test_reward_py_imports_only_the_standard_library(bundle: dict[str, Any]) -> None:
    tree = ast.parse((bundle["dir"] / "reward.py").read_text())
    modules = {
        (n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
        for n in ast.walk(tree)
        if isinstance(n, ast.Import | ast.ImportFrom)
        for a in (n.names if isinstance(n, ast.Import) else [n])
    }
    assert modules and modules <= set(sys.stdlib_module_names) | {"__future__"}, modules


@pytest.mark.parametrize(
    ("style", "code"),
    [
        ("pairwise", "reward_rubric_style_unsupported"),
        ("stepwise", "reward_rubric_style_unsupported"),
    ],
)
async def test_rubric_styles_other_than_pointwise_and_binary_are_refused(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    data_dir: Path,
    style: str,
    code: str,
) -> None:
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    body = {**BINARY, "style": style}
    if style == "pairwise":
        body.update(
            {
                "pair_fields": ["a", "b"],
                "swap_map": {"yes": "no", "no": "yes"},
                "input_fields": ["a", "b"],
                "messages": [{"role": "user", "content": "A: {a} B: {b} VERDICT: yes or no"}],
            }
        )
    rubric_id = await rubric(client, f"pun/{style}", body)
    r = await export(
        client, {"version_id": version_id, "rubric_id": rubric_id, "judge_model_id": "j"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == code


async def test_the_scale_must_cover_every_verdict_unless_every_verdict_is_a_number(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    words = await rubric(client, "pun/words", BINARY)
    base = {"version_id": version_id, "rubric_id": words, "judge_model_id": "j"}
    r = await export(client, base)
    assert r.status_code == 422 and r.json()["error"]["code"] == "reward_scale_required"
    r = await export(client, {**base, "verdict_scores": {"yes": 1.0}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "reward_scale_incomplete"
    assert r.json()["error"]["details"]["missing"] == ["no"]
    numbers = await rubric(client, "pun/scale", SCALE)
    r = await export(client, {**base, "rubric_id": numbers})
    assert r.status_code == 202, r.text
    r = await export(
        client, {**base, "verdict_scores": {"yes": 1.0, "no": 0.0}, "judge_model_id": " "}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "reward_judge_required"


#: The shape found live on 2026-10-17 (production rubric ``highstakes-verdict-v1@3``): a LABELING
#: rubric, JSON answer with escaped braces in the system message, the row in ``{text}``.
LABELING_TEXT = {
    "style": "pointwise",
    "messages": [
        {"role": "system", "content": 'Rate it. Reply ONLY {{"verdict": "high"|"low"}}'},
        {"role": "user", "content": "{text}"},
    ],
    "input_fields": ["text"],
    "parser": "json_v1",
    "json_schema": {"type": "object", "properties": {"verdict": {"enum": ["high", "low"]}}},
    "allowed_verdicts": ["high", "low"],
}


async def test_a_rubric_field_the_reward_function_cannot_fill_is_refused(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    """The live finding: this exported 202 and its reward.py raised on the first completion."""
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    rubric_id = await rubric(client, "stakes/labeling", LABELING_TEXT)
    r = await export(
        client,
        {
            "version_id": version_id,
            "rubric_id": rubric_id,
            "judge_model_id": "j",
            "verdict_scores": {"high": 1.0, "low": 0.0},
        },
    )
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "reward_rubric_fields_unfillable"
    assert error["details"]["unfillable"] == ["text"]
    assert error["details"]["fillable"] == ["completion", "prompt", "question", "topic"]


async def test_the_check_relies_on_005_refusing_undeclared_placeholders(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    """``fields_or_refuse`` reads ``input_fields`` only. That is complete because 005 refuses a
    message placeholder that is not declared; if 005 ever accepted one, the bundle could render a
    field the check never saw, so this precondition is asserted beside the check."""
    body = {
        **BINARY,
        "messages": [{"role": "user", "content": "{completion} in {genre}\nVERDICT: yes or no."}],
        "input_fields": ["completion"],
    }
    r = await client.post("/api/v1/rubrics", json={"name": "pun/undeclared", "body": body})
    assert r.status_code == 422, r.text
    error = r.json()["error"]
    assert error["code"] == "RUBRIC_TEMPLATE_INVALID" and error["details"]["name"] == "genre"


async def test_a_rubric_that_never_reads_the_completion_is_refused(
    client: httpx.AsyncClient, operator_name: str, driver: BuildDriver, data_dir: Path
) -> None:
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    body = {
        **BINARY,
        "messages": [{"role": "user", "content": "Prompt: {prompt}\nVERDICT: yes or no."}],
        "input_fields": ["prompt"],
    }
    rubric_id = await rubric(client, "pun/prompt-only", body)
    r = await export(
        client,
        {
            "version_id": version_id,
            "rubric_id": rubric_id,
            "judge_model_id": "j",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "reward_rubric_ignores_completion"


async def test_a_version_column_fills_a_rubric_field_end_to_end(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    judge: type[Judge],
    tmp_path: Path,
) -> None:
    """``topic`` is a column of the version, so it is accepted AND the exported reward.py renders
    it from TRL's keyword list (the accepted set is what reward.py really fills)."""
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    body = {
        **BINARY,
        "messages": [{"role": "user", "content": "Topic {topic}: {completion}\nVERDICT: yes"}],
        "input_fields": ["topic", "completion"],
    }
    rubric_id = await rubric(client, "pun/topic", body)
    r = await export(
        client,
        {
            "version_id": version_id,
            "rubric_id": rubric_id,
            "judge_model_id": "j",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
        },
    )
    assert r.status_code == 202, r.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{r.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    reward = load(export_dir(row["id"]), tmp_path)
    judge.script = [(200, "VERDICT: yes", {})]
    assert reward.judge_reward(prompts=["p"], completions=["c-text"], topic=["cats"]) == [1.0]
    assert judge.seen[0]["body"]["messages"][0]["content"] == "Topic cats: c-text\nVERDICT: yes"


# --- the reward function itself, as TRL calls it ------------------------------------------------


class Judge(BaseHTTPRequestHandler):
    """Answers from a script: each entry is (status, body or None, headers)."""

    script: list[tuple[int, str | None, dict[str, str]]] = []
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        length = int(self.headers.get("Content-Length", "0"))
        Judge.seen.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": json.loads(self.rfile.read(length)),
            }
        )
        status, content, headers = (
            Judge.script.pop(0) if Judge.script else (200, "VERDICT: yes", {})
        )
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if content is not None:
            payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
            self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def judge(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[Judge]]:
    server = HTTPServer(("127.0.0.1", 0), Judge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Judge.script, Judge.seen = [], []
    monkeypatch.setenv("MIDATAWORKS_JUDGE_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("MIDATAWORKS_JUDGE_API_KEY", "k-test")
    yield Judge
    server.shutdown()


def load(bundle_dir: Path, tmp_path: Path) -> ModuleType:
    for name in ("reward.py", "rubric.json", "config.json"):
        (tmp_path / name).write_bytes((bundle_dir / name).read_bytes())
    spec = importlib.util.spec_from_file_location(f"reward_{tmp_path.name}", tmp_path / "reward.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG["backoff_s"] = 0.0
    return module


async def test_trl_calls_it_with_its_signature_and_gets_one_float_per_completion(
    bundle: dict[str, Any], judge: type[Judge], tmp_path: Path
) -> None:
    reward = load(bundle["dir"], tmp_path)
    judge.script = [(200, "fine\nVERDICT: yes", {}), (200, "VERDICT: no", {})]
    prompts = [[{"role": "user", "content": "pun about cats"}], [{"role": "user", "content": "x"}]]
    completions = [
        [{"role": "assistant", "content": "purr-fect"}],
        [{"role": "assistant", "content": "no pun"}],
    ]
    out = reward.judge_reward(
        prompts=prompts, completions=completions, completion_ids=[[1, 2], [3]], topic=["t0", "t1"]
    )
    assert out == [1.0, 0.0] and all(isinstance(x, float) for x in out)
    first = judge.seen[0]
    assert first["path"] == "/v1/chat/completions" and first["auth"] == "Bearer k-test"
    assert first["body"]["model"] == "judge-7b" and first["body"]["temperature"] == 0
    assert [s["body"]["seed"] for s in judge.seen] == [11, 12]
    assert "purr-fect" in first["body"]["messages"][0]["content"]
    assert "pun about cats" in first["body"]["messages"][0]["content"]


async def test_503_with_retry_after_is_retried_then_answers(
    bundle: dict[str, Any], judge: type[Judge], tmp_path: Path
) -> None:
    reward = load(bundle["dir"], tmp_path)
    judge.script = [(503, None, {"Retry-After": "0"}), (200, "VERDICT: yes", {})]
    assert reward.judge_reward(prompts=["p"], completions=["c"]) == [1.0]
    assert len(judge.seen) == 2


async def test_unparseable_answers_are_counted_retried_then_raise_never_zero(
    bundle: dict[str, Any], judge: type[Judge], tmp_path: Path
) -> None:
    reward = load(bundle["dir"], tmp_path)
    judge.script = [(200, "I think yes", {})] * 3
    with pytest.raises(reward.RewardJudgeError, match="3 attempts"):
        reward.judge_reward(prompts=["p"], completions=["c"])
    assert reward.PARSE_FAILURES == 3 and len(judge.seen) == 3


async def test_the_bundle_rubric_json_is_the_rubric_export(
    bundle: dict[str, Any], client: httpx.AsyncClient
) -> None:
    """015 FTID I1: rubric.json comes from the one midataworks.rubric/v1 serialiser; a keyless
    rubric's bytes are what the bundle wrote before feature 015."""
    from src.core.canonical_json import canonical_json

    written = (bundle["dir"] / "rubric.json").read_bytes()
    rubric_id = bundle["row"]["params"]["rubric_id"]
    exported = (await client.get(f"/api/v1/rubrics/{rubric_id}/export")).json()
    assert written == canonical_json(exported)
    assert set(json.loads(written)) == {"format", "name", "version", "body"}


async def test_a_keyed_rubric_bundle_carries_its_behaviour(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    r = await client.post(
        "/api/v1/rubrics", json={"name": "pun/keyed", "body": BINARY, "behaviour_key": "pun"}
    )
    assert r.status_code == 201, r.text
    started = await export(
        client,
        {
            "version_id": version_id,
            "rubric_id": r.json()["id"],
            "judge_model_id": "judge-7b",
            "verdict_scores": {"yes": 1.0, "no": 0.0},
        },
    )
    assert started.status_code == 202, started.text
    publisher.run_publish_tasks()
    row = (await client.get(f"/api/v1/exports/{started.json()['export_id']}")).json()
    assert row["status"] == "completed", row
    doc = json.loads((export_dir(row["id"]) / "rubric.json").read_bytes())
    assert doc["behaviour"] == "pun"
