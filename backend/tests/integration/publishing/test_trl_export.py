"""TRL-ready exports (FR-008.25–008.29; 008 FTASKS 10.2–10.5; AC-US4; EC-11, EC-12).

Each type is exported, loaded back with ``datasets``, and compared with TRL's column contract at
the pin. 004's validator is not built, so the default is a refusal; the writing path runs with a
stand-in validator that answers "valid" (and one that answers "invalid").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import datasets
import httpx
import pytest

from src.core.database import sync_session_factory
from src.models import Job
from src.services.exports.trl_contracts import CONTRACTS
from src.services.publishing import feature_seams as seams
from src.services.publishing.manifest_builder import validate_against_file
from tests.support.publish_fixtures import PublishDriver, publisher
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver", "publisher"]

ROWS: dict[str, list[dict[str, Any]]] = {
    "dpo": [
        {"prompt": f"Tell a joke {i}", "chosen": f"Why did {i} cross?", "rejected": f"No {i}."}
        for i in range(6)
    ],
    "kto": [
        {"prompt": f"Joke {i}", "completion": f"Punchline {i}", "label": i % 2 == 0}
        for i in range(6)
    ],
    "prm": [
        {
            "prompt": f"Is 9.{i} > 9.8?",
            "completions": ["9.1 < 9.8", f"so no {i}"],
            "labels": [True, i % 2 == 0],
        }
        for i in range(6)
    ],
    "sft": [
        {
            "messages": [
                {"role": "user", "content": f"hi {i}"},
                {"role": "assistant", "content": "hello"},
            ]
        }
        for i in range(6)
    ],
    "grpo_prompt": [{"prompt": f"Write a pun about {i}", "topic": f"t{i}"} for i in range(6)],
}
CONTENT = {
    "dpo": ("prompt", "chosen", "rejected"),
    "kto": ("prompt", "completion"),
    "prm": ("prompt", "completions"),
    "sft": ("messages",),
    "grpo_prompt": ("prompt",),
}


def validator(monkeypatch: pytest.MonkeyPatch, valid: bool = True) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def fake(version_id: str, target_type: str, *, session: Any) -> seams.TrlFinding:
        seen.append((version_id, target_type))
        return seams.TrlFinding(
            seams.CHECKED, valid, [] if valid else ["row 2: chosen equals rejected"]
        )

    monkeypatch.setattr(seams, "validate_trl", fake)
    return seen


async def version_of(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, kind: str
) -> str:
    source = make_source(
        data_dir,
        {"train": ROWS[kind]},
        repo_id=f"org/{kind.replace('_', '-')}",
        text_columns=CONTENT[kind],
    )
    name = f"trl-{kind.replace('_', '-')}"
    ds = await client.post("/api/v1/datasets", json={"name": name, "target_type": "untyped"})
    rec = await client.post(
        "/api/v1/recipes", json={"name": f"{name}-r", "body": body(("stub_keep", {}))}
    )
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds.json()["id"],
            "recipe_revision_id": rec.json()["head_revision_id"],
            "inputs": [{"kind": "source", "source_id": source}],
        },
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result is not None
        return str(job.result["version_id"])


async def export(
    client: httpx.AsyncClient, publisher: PublishDriver, payload: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post("/api/v1/exports", json=payload)
    assert response.status_code == 202, response.text
    publisher.run_publish_tasks()
    data: dict[str, Any] = (
        await client.get(f"/api/v1/exports/{response.json()['export_id']}")
    ).json()
    return data


async def test_with_004_the_real_validator_admits_a_valid_dpo_version(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """004 landed (2026-10-07): the export is gated by 004's real check mode, not refused as
    ``trl_validator_unavailable``. A valid DPO version is accepted (202)."""
    version_id = await version_of(client, driver, data_dir, "dpo")
    response = await client.post(
        "/api/v1/exports", json={"target": "trl", "version_id": version_id, "trl_type": "dpo"}
    )
    assert response.status_code == 202, response.text


async def test_a_failing_validator_refuses_with_its_first_failures(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator(monkeypatch, valid=False)
    version_id = await version_of(client, driver, data_dir, "dpo")
    response = await client.post(
        "/api/v1/exports", json={"target": "trl", "version_id": version_id, "trl_type": "dpo"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "trl_validation_failed"
    assert "chosen equals rejected" in response.json()["error"]["message"]


async def test_ec11_an_unformable_type_names_the_missing_column(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator(monkeypatch)
    version_id = await version_of(client, driver, data_dir, "grpo_prompt")
    for trl_type, missing in (
        ("dpo", "chosen"),
        ("kto", "completion"),
        ("prm", "completions"),
        ("sft", "messages"),
    ):
        response = await client.post(
            "/api/v1/exports",
            json={"target": "trl", "version_id": version_id, "trl_type": trl_type},
        )
        assert response.status_code == 422, trl_type
        assert response.json()["error"]["code"] == "trl_type_unformable"
        assert response.json()["error"]["details"]["missing"][0] == missing


@pytest.mark.parametrize("kind", ["dpo", "kto", "prm", "sft", "grpo_prompt"])
@pytest.mark.parametrize("fmt", ["parquet", "jsonl"])
async def test_each_type_loads_with_exactly_trls_keys(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    fmt: str,
) -> None:
    seen = validator(monkeypatch)
    version_id = await version_of(client, driver, data_dir, kind)
    payload: dict[str, Any] = {
        "target": "trl",
        "version_id": version_id,
        "trl_type": kind,
        "format": fmt,
    }
    if kind == "grpo_prompt":
        payload["extra_columns"] = ["topic"]
    result = await export(client, publisher, payload)
    assert result["status"] == "completed", result["error"]
    assert seen and seen[-1] == (version_id, kind), "004's validator ran, in the worker too"
    out = data_dir / "exports" / result["id"]
    loaded = (
        datasets.Dataset.from_parquet(str(out / f"train.{fmt}"))
        if fmt == "parquet"
        else datasets.load_dataset("json", data_files=str(out / "train.jsonl"), split="train")
    )
    expected = list(CONTRACTS[kind].variants[0].columns) + (
        ["topic"] if kind == "grpo_prompt" else []
    )
    assert loaded.column_names == expected
    assert loaded.num_rows == 6
    if kind == "kto":
        assert all(isinstance(v, bool) for v in loaded["label"])
    if kind == "prm":
        assert all(isinstance(v, bool) for row in loaded["labels"] for v in row)
    manifest = (out / "midataworks-dataset-version.json").read_bytes()
    validate_against_file(manifest)
    doc = json.loads(manifest)
    assert doc["target"]["kind"] == "trl_export" and doc["target"]["trl_type"] == kind
    assert doc["target"]["trl_version"] == ("0.29.1" if kind == "prm" else "1.14.1")
    assert [c["name"] for c in doc["content"]["columns"]] == expected
    # D7: an export carries the same lineage facts as a publish (here: no generated row)
    assert doc["extensions"]["lineage"]["generated_rows"] == 0
    keys = datasets.Dataset.from_parquet(str(out / "train.row_keys.parquet"))
    assert keys.column_names == ["_dw_row_key", "_dw_occurrence"] and keys.num_rows == 6


async def test_a_file_outside_the_export_is_never_served(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator(monkeypatch)
    version_id = await version_of(client, driver, data_dir, "dpo")
    result = await export(
        client, publisher, {"target": "trl", "version_id": version_id, "trl_type": "dpo"}
    )
    ok = await client.get(f"/api/v1/exports/{result['id']}/files/train.parquet")
    assert ok.status_code == 200 and ok.content[:4] == b"PAR1"
    for bad in (
        "..%2F..%2Fsecret",
        "%2E%2E%2Fmanifest.json",
        "train.row_keys.parquet.bak",
        "%2Fetc%2Fpasswd",
    ):
        response = await client.get(f"/api/v1/exports/{result['id']}/files/{bad}")
        assert response.status_code == 404, bad


async def test_a_held_out_split_is_written_separately_and_marked(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.publish_fixtures import built_version

    validator(monkeypatch)
    version_id = await built_version(client, driver, data_dir)  # text, label; held-out "test"
    result = await export(
        client,
        publisher,
        {
            "target": "miforge_set",
            "version_id": version_id,
            "miforge_set_kind": "corpus",
            "label_column": "label",
        },
    )
    assert result["status"] == "completed", result["error"]
    doc = json.loads(
        (data_dir / "exports" / result["id"] / "midataworks-dataset-version.json").read_bytes()
    )
    splits = {s["name"]: s for s in doc["content"]["splits"]}
    assert splits["test"]["held_out"] and splits["test"]["evaluation_only"]
    assert not splits["train"]["held_out"]
    assert doc["target"]["miforge_set_kind"] == "corpus"
    assert doc["target"]["contract_reference"] == {
        "type": "corpus",
        "name": "humor",
        "version": "v1",
    }
    assert {f["path"] for f in result["files"]} == {
        "train.parquet",
        "train.row_keys.parquet",
        "test.parquet",
        "test.row_keys.parquet",
    }
