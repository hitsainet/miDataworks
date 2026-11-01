"""``GET /api/v1/sources/meta`` is the one place the UI reads 001's vocabularies and limits
(001 FTASKS 10.5; FTDD 5.2). Every list is derived from its source of truth, so a changed enum or a
changed setting changes the answer; no frontend list can drift."""

from __future__ import annotations

import httpx
import pytest

from src.models.enums import TargetType, values
from src.models.source_enums import AnnotationKind, Redistribution, SourceKind, SourceState
from src.services.sources import detection, preview_service, upload_service


async def test_meta_serves_each_vocabulary_from_its_source(client: httpx.AsyncClient) -> None:
    meta = (await client.get("/api/v1/sources/meta")).json()
    assert meta["kinds"] == values(SourceKind) == ["hf", "upload"]
    assert meta["states"] == values(SourceState)
    assert meta["annotation_kinds"] == values(AnnotationKind)
    assert meta["redistribution"] == values(Redistribution)
    assert meta["chat_formats"] == list(detection.CHAT_FORMATS)
    assert meta["trl_types"] == list(detection.TRL_TYPES)
    assert meta["target_types"] == values(TargetType)
    assert meta["csv_defaults"] == upload_service.CSV_DEFAULTS
    assert meta["limits"]["preview_sample_rows"] == preview_service.SAMPLE_LIMIT == 100
    assert meta["limits"]["upload_max_bytes"] == 2 * 1024**3


async def test_meta_follows_a_changed_vocabulary(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(detection, "CHAT_FORMATS", (*detection.CHAT_FORMATS, "new_format"))
    meta = (await client.get("/api/v1/sources/meta")).json()
    assert meta["chat_formats"][-1] == "new_format"


async def test_meta_follows_the_storage_settings(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    put = await client.put("/api/v1/settings/upload_max_bytes", json={"value": "1048576"})
    assert put.status_code == 200, put.text
    meta = (await client.get("/api/v1/sources/meta")).json()
    assert meta["limits"]["upload_max_bytes"] == 1_048_576


@pytest.mark.parametrize(
    ("columns", "rows"),
    [
        ([("text", "string"), ("humor", "bool")], [{"text": "a joke", "humor": True}] * 5),
        (
            [("prompt", "string"), ("chosen", "string"), ("rejected", "string")],
            [{"prompt": "q", "chosen": "good", "rejected": "bad"}] * 5,
        ),
        (
            [("messages", "list")],
            [
                {
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "yo"},
                    ]
                }
            ]
            * 5,
        ),
        ([("a", "string"), ("b", "string")], [{"a": "x", "b": "y"}] * 5),
    ],
)
def test_detect_answers_only_in_the_served_vocabulary(
    columns: list[tuple[str, str]], rows: list[dict[str, object]]
) -> None:
    """The meta lists are the vocabulary ``detect`` answers in, not a copy that could disagree."""
    result = detection.detect(columns, rows).as_dict()
    assert result["trl_type"] in detection.TRL_TYPES
    assert result["chat_format"] in detection.CHAT_FORMATS
    assert result["trl_format"] in (*detection.TRL_FORMATS, None)
