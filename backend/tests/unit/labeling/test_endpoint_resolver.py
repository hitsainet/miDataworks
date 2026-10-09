"""The endpoint-role contract (005 FTASKS 5.1 – 5.3; FPRD 005 section 7.4 item 3)."""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.core.encryption import encrypt_value
from src.services.endpoint_resolver import RoleUnconfigured, resolve_from_rows

SRC = Path(__file__).resolve().parents[3] / "src"


@dataclass
class Row:
    role: str
    protocol: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    api_key_ciphertext: str | None = None
    inherit_from_judge: bool = False
    use_mode: str = "own"


def rows(**kw: Row) -> dict[str, Row]:
    return kw


CLS = Row(
    "classifier", "openai_scoring", "http://c/v1", "JEV-9B-decision", encrypt_value("key-c-123456")
)
JUDGE = Row("judge", "openai_chat", "http://j/v1", "Qwen2.5-7B", encrypt_value("key-j-123456"))


def test_classifier_own() -> None:
    r = resolve_from_rows("classifier", rows(classifier=CLS))
    assert (r.base_url, r.model_id, r.api_key, r.inherited_from) == (
        "http://c/v1",
        "JEV-9B-decision",
        "key-c-123456",
        None,
    )


def test_judge_same_as_classifier_keeps_its_own_model_and_protocol() -> None:
    judge = Row("judge", "openai_chat", None, "Qwen2.5-7B", use_mode="same_as_classifier")
    r = resolve_from_rows("judge", rows(classifier=CLS, judge=judge))
    assert r.base_url == "http://c/v1" and r.api_key == "key-c-123456"
    assert r.model_id == "Qwen2.5-7B" and r.model_id != CLS.model_id
    assert r.protocol == "openai_chat" and r.inherited_from == "classifier"


def test_judge_none_is_unconfigured_naming_the_field() -> None:
    with pytest.raises(RoleUnconfigured) as exc:
        resolve_from_rows("judge", rows(classifier=CLS, judge=Row("judge", use_mode="none")))
    assert exc.value.code == "ROLE_UNCONFIGURED"
    assert exc.value.details["setting"] == "endpoint_roles.judge.use_mode"


@pytest.mark.parametrize(
    ("judge_use", "child", "child_set"),
    list(
        itertools.product(
            ["own", "same_as_classifier", "none"], ["generation", "embeddings"], [True, False]
        )
    ),
)
def test_matrix(judge_use: str, child: str, child_set: bool) -> None:
    judge = Row(
        "judge",
        "openai_chat",
        "http://j/v1" if judge_use == "own" else None,
        "Qwen2.5-7B",
        encrypt_value("key-j-123456"),
        use_mode=judge_use,
    )
    own = Row(
        child,
        "openai_embeddings" if child == "embeddings" else "openai_chat",
        "http://own/v1",
        "own-model",
        inherit_from_judge=False,
    )
    inherit = Row(child, None, None, None, inherit_from_judge=True)
    table = rows(classifier=CLS, judge=judge, **{child: own if child_set else inherit})
    if child_set:
        r = resolve_from_rows(child, table)
        assert (r.base_url, r.model_id) == ("http://own/v1", "own-model")
    elif judge_use == "none":
        with pytest.raises(RoleUnconfigured) as exc:
            resolve_from_rows(child, table)
        assert exc.value.details["role"] == child
    else:
        r = resolve_from_rows(child, table)
        expected_url = "http://j/v1" if judge_use == "own" else "http://c/v1"
        assert r.base_url == expected_url and r.model_id == "Qwen2.5-7B"
        assert r.protocol == ("openai_chat" if child == "generation" else "openai_embeddings")


def test_generation_protocol_is_fixed_by_role() -> None:
    gen = Row("generation", "something_else", "http://g/v1", "g-model")
    assert resolve_from_rows("generation", rows(generation=gen)).protocol == "openai_chat"


@pytest.mark.parametrize(
    ("row", "field"),
    [
        (Row("classifier"), "base_url"),
        (Row("classifier", "openai_scoring", "http://c"), "model_id"),
    ],
)
def test_unconfigured_names_the_field(row: Row, field: str) -> None:
    with pytest.raises(RoleUnconfigured) as exc:
        resolve_from_rows("classifier", rows(classifier=row))
    assert exc.value.details["setting"] == f"endpoint_roles.classifier.{field}"


def test_the_snapshot_never_carries_the_key() -> None:
    snapshot = resolve_from_rows("classifier", rows(classifier=CLS)).snapshot()
    assert "key-c-123456" not in str(snapshot) and "api_key" not in snapshot


def test_only_the_resolver_reads_endpoint_roles_for_resolution() -> None:
    """005 FTASKS 5.3. Foundation's storage (model, service, its routes, Fetch models) may read
    the table; no other module resolves endpoints by reading it."""
    allowed = {
        "models/endpoint_role.py",
        "models/__init__.py",
        "services/endpoint_role_service.py",
        "services/endpoint_resolver.py",
        "api/v1/endpoints/endpoint_roles.py",
        # Foundation 8.5's key decrypter (reads a role's key, resolves nothing).
        "workers/secrets.py",
    }
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = str(path.relative_to(SRC))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (
                    node.module.endswith("models.endpoint_role")
                    or node.module.endswith("endpoint_role_service")
                    or node.module == "endpoint_role"
                )
            ):
                if rel not in allowed:
                    offenders.append(rel)
    assert offenders == []
