"""Feature 005's settings are deployed with the values the design states (005 FTASKS 15.1)."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.core.config import Settings

REPO = Path(__file__).resolve().parents[4]

DEPLOYED = {
    "LABEL_CHUNK_SIZE": "200",
    "LABEL_MAX_CONSECUTIVE_FAILURES": "20",
    "LABEL_BACKOFF_CAP_SECONDS": "60",
    "JUDGE_PARSE_FAILURE_STOP_SHARE": "0.05",
    "KEEP_SHARE_SAMPLE_ROWS": "400",
    "MILLM_LEASE_TTL_SECONDS": "1800",
    "MILLM_LEASE_HOLDER": "midataworks",
}


def test_the_configmap_deploys_each_value() -> None:
    docs = [d for d in yaml.safe_load_all((REPO / "k8s/base/config.yaml").read_text()) if d]
    (config,) = [d for d in docs if d["kind"] == "ConfigMap"]
    for name, value in DEPLOYED.items():
        assert str(config["data"][name]) == value, name


def test_the_defaults_match_the_deployed_values() -> None:
    fields = Settings.model_fields
    for name, value in DEPLOYED.items():
        default = fields[name.lower()].default
        assert str(default) in (value, value + ".0"), name


def test_env_example_lists_them() -> None:
    example = (REPO / ".env.example").read_text()
    for name in DEPLOYED:
        assert f"\n{name}=" in example, name
