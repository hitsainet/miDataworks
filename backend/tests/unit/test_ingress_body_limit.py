"""The ingress lets an upload of the default cap through (001 FTASKS 12.3; FTDD 11).

If ``proxy-body-size`` were below ``UPLOAD_MAX_BYTES`` plus multipart overhead, nginx would answer
its own 413 before the app could refuse with ``upload_too_large`` and a next step; uploads within
the advertised limit would fail with an HTML page. ``0`` means unlimited.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core.config import Settings

REPO = Path(__file__).resolve().parents[3]
UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
OVERHEAD = 1024**2  # multipart boundaries and the manifest part


def nginx_size(value: str) -> float:
    match = re.fullmatch(r"(\d+)([kmgKMG]?)", value.strip())
    assert match, f"unparsable proxy-body-size {value!r}"
    number, unit = int(match.group(1)), match.group(2).lower()
    return float("inf") if number == 0 else number * UNITS[unit]


def api_ingress() -> dict[str, Any]:
    docs = list(yaml.safe_load_all((REPO / "k8s/base/ingress.yaml").read_text()))
    return next(d for d in docs if d and d["metadata"]["name"] == "midataworks-ingress")


def test_the_body_limit_admits_the_default_upload_cap() -> None:
    annotations = api_ingress()["metadata"]["annotations"]
    limit = nginx_size(annotations["nginx.ingress.kubernetes.io/proxy-body-size"])
    default = Settings.model_fields["upload_max_bytes"].default
    assert default == 2 * 1024**3
    assert limit >= default + OVERHEAD


def test_uploads_stream_through_the_ingress() -> None:
    annotations = api_ingress()["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-request-buffering"] == "off"


@pytest.mark.parametrize(
    ("value", "ok"), [("0", True), ("3g", True), ("2g", False), ("100m", False)]
)
def test_the_parser_reads_nginx_sizes(value: str, ok: bool) -> None:
    assert (nginx_size(value) >= 2 * 1024**3 + OVERHEAD) is ok


def test_upload_spooling_and_import_temporaries_land_on_the_data_volume() -> None:
    docs = list(yaml.safe_load_all((REPO / "k8s/base/backend.yaml").read_text()))
    pod = next(d for d in docs if d and d["kind"] == "Deployment")["spec"]["template"]["spec"]
    by_name = {c["name"]: c for c in pod["containers"]}
    for name in ("api", "worker-curation"):
        env = {e["name"]: e.get("value") for e in by_name[name]["env"]}
        assert env.get("TMPDIR") == "/data/dataworks/tmp", name
    init = " ".join(pod["initContainers"][0]["command"])
    assert " tmp" in init, "the init container creates and owns tmp/"


def test_feature_001_settings_default_as_documented() -> None:
    fields = Settings.model_fields
    expected = {
        "hf_hub_url": "https://huggingface.co",
        "hf_datasets_server_url": "https://datasets-server.huggingface.co",
        "hf_http_timeout_s": 30.0,
        "preview_timeout_s": 30.0,
        "ephemeral_secret_ttl_s": 900,
        "source_import_janitor_limit_s": 900,
        "upload_max_bytes": 2 * 1024**3,
        "import_confirm_bytes": 50_000_000_000,
    }
    for name, value in expected.items():
        assert fields[name].default == value, name
