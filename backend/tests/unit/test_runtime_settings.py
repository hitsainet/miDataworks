"""Runtime environment settings: the registry, the overlay and the readers (feature 012).

ENH-003 decisions D2 (the allow-list), D7 (no dead settings), D10 (one mechanism), D11 (the
approval rules stay deployment configuration). The routes and the wiring are tested in
``tests/integration/test_runtime_settings_*.py``.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest

from src.core import runtime_settings as rs
from src.core.config import Settings, env_settings, get_settings
from src.mcp_server.config import MCPSettings

SRC = Path(__file__).resolve().parents[2] / "src"
#: Feature 016's caps (FR-016.37; FTDD 016 section 8.3): each runtime-changeable, "next request".
RULE_SET_CAPS = (
    "rule_set_max_signals",
    "rule_set_max_cases",
    "rule_set_max_terms",
    "rule_set_max_code_params_bytes",
    "rule_set_max_document_bytes",
    "rule_preview_max_text_bytes",
    "recipe_max_rule_sets",
)
#: Slice 4: the cap is read at start ("next request"); the copied values and the time limit at the
#: next run.
RULE_DERIVATION_KEYS = {
    "rule_derivation_max_signals": "next request",
    "rule_derivation_min_held_out_per_class": "next run",
    "rule_derivation_max_source_rows": "next run",
    "rule_derivation_time_limit_s": "next run",
}
#: Feature 017 (FR-017.54, FR-017.64; FTID 017 section 9): the behaviour caps, read per request.
BEHAVIOUR_KEYS = {
    "behaviour_max_held_out_values": "next request",
    "behaviour_max_vocabulary_values": "next request",
    "behaviour_max_subquestions": "next request",
    "second_opinion_no_rule_negatives": "next request",
    "generation_requests_in_flight": "next run",
    # slice 6 (FR-017.38 - FR-017.44, FR-017.64)
    "second_opinion_instructed_rate": "next request",
    "second_opinion_instructed_min": "next request",
    "classifier_run_cap_rows": "next check in a running run",
    "classifier_spend_ceiling_usd": "next check in a running run",
    "review_max_queues_per_handoff": "next request",
    # slice 7 (TD-017.29)
    "detector_max_sets_per_send": "next request",
}
#: Feature 018 (FTDD §8.3; FTASKS 10.11): operator code rules. The switch is operator-only.
CODE_RULE_KEYS = {
    "code_rules_enabled": "next request",
    "code_rule_call_fuel": "next request",
    "code_rule_load_fuel": "next request",
    "code_rule_memory_bytes": "next request",
    "code_rule_input_code_points": "next request",
    "code_rule_output_bytes": "next request",
    "code_rule_wall_ms": "next request",
    "code_rule_api_request_s": "next request",
    # 018 slice 4: read by the build's use recorder (services/code_rules/uses.py), not before.
    "code_rule_failure_share": "next run",
    "code_rule_dry_run_per_minute": "next request",
    "code_rule_dry_run_max_texts": "next request",
    "code_rule_max_source_bytes": "next request",
    "code_rule_max_functions": "next request",
    "code_rule_max_cases": "next request",
    "code_rule_max_param_schema_bytes": "next request",
}
#: Each runner limit and the contract floor it may never go below (M30).
FLOORED = {
    "code_rule_call_fuel": "call_fuel",
    "code_rule_load_fuel": "load_fuel",
    "code_rule_memory_bytes": "memory_bytes",
    "code_rule_input_code_points": "input_code_points",
    "code_rule_output_bytes": "output_bytes",
}


# --- the registry ------------------------------------------------------------------------------


def test_every_listed_key_is_a_settings_field() -> None:
    fields = set(Settings.model_fields)
    for group in (rs.RUNTIME_KEYS, rs.RESTART_KEYS, rs.APPROVAL_RULE_KEYS, rs.NEVER_WRITABLE):
        assert set(group) <= fields, set(group) - fields


def test_the_groups_do_not_overlap() -> None:
    groups = {
        "runtime": set(rs.RUNTIME_KEYS),
        "restart": set(rs.RESTART_KEYS),
        "approval_rule": set(rs.APPROVAL_RULE_KEYS),
        "never": set(rs.NEVER_WRITABLE),
    }
    names = sorted(groups)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            assert not groups[a] & groups[b], (a, b, groups[a] & groups[b])


def test_the_allow_list_is_the_decided_one() -> None:
    """ENH-003 D2: §3.3's table (26 keys) plus the preview and sample limit group (9)."""
    assert len(rs.RUNTIME_KEYS) == (
        35
        + len(RULE_SET_CAPS)
        + len(RULE_DERIVATION_KEYS)
        + len(BEHAVIOUR_KEYS)
        + len(CODE_RULE_KEYS)
    )
    preview_group = {
        "operator_preview_sample_default",
        "operator_preview_sample_max",
        "operator_model_preview_sample_default",
        "operator_model_preview_sample_max",
        "label_sample_max_rows",
        "keep_share_sample_rows",
        "keep_share_max_rows",
        "generation_preview_max_prompts",
        "generation_preview_timeout_seconds",
    }
    assert preview_group <= set(rs.RUNTIME_KEYS)


def test_secrets_and_infrastructure_are_never_writable_by_name() -> None:
    """ENH-003 §3.3 "Excluded". Moving one of these into RUNTIME_KEYS turns this red."""
    must = {
        "database_url",
        "redis_url",
        "settings_encryption_key",
        "internal_api_secret",
        "designer_handoff_key",
        "dataworks_api_url",
        "mcp_internal_url",
        "data_dir",
        "hf_hub_url",
        "hf_datasets_server_url",
        "environment",
        "celery_task_always_eager",
        "operator_test_fixtures",
        "app_build",
        "reward_bundle_trl_version",
        "parquet_row_group_rows",
        "publish_build_batch_rows",
        "millm_lease_holder",
        "generation_engine_path",
    }
    assert must <= rs.NEVER_WRITABLE
    assert not must & set(rs.RUNTIME_KEYS)
    assert rs.SECRET_KEYS <= rs.NEVER_WRITABLE


def test_the_approval_rules_stay_deployment_configuration() -> None:
    """D11 keeps P-08: none of the gate or budget keys is runtime-writable."""
    assert set(rs.APPROVAL_RULE_KEYS) == {
        "agent_label_row_threshold",
        "agent_label_window_hours",
        "approval_ttl_hours",
        "detector_length_overlap_min",
        "judge_parse_failure_stop_share",
        "judge_parse_failure_min_rows",
    }


def test_operator_only_keys_are_refused_to_agents() -> None:
    forbidden = {k for k, v in rs.RUNTIME_KEYS.items() if v.agent_forbidden}
    assert forbidden == {
        "millm_steering_supported",
        "millm_base_url",
        "mistudio_base_url",
        "millm_ui_url",
        # 017 (FTID I16): an agent may not raise its own spending ceiling
        "classifier_spend_ceiling_usd",
        # 018 (H2 (a)): code rules run only when the operator says so
        "code_rules_enabled",
    }


#: Each key's stated effect timing, pinned (ENH-003 §2 "Effect timing shown"). The wiring tests
#: exercise one key per mechanism: next request (upload cap), next task (task_prerun), next janitor
#: pass (janitor), next check in a running run (chunk boundary), now (log level).
EFFECTS: dict[str, str] = {
    "endpoint_http_timeout_seconds": "next run",
    "label_max_consecutive_failures": "next check in a running run",
    "label_transient_retries": "next run",
    "label_backoff_cap_seconds": "next run",
    "millm_lease_ttl_seconds": "next lease grant or renewal",
    "label_batch_poll_seconds": "next poll",
    "detector_send_poll_seconds": "next poll",
    "detector_send_download_timeout_minutes": "next send step",
    "version_build_janitor_minutes": "next janitor pass",
    "source_import_janitor_limit_s": "next janitor pass",
    "publish_janitor_limit_seconds": "next janitor pass",
    "detector_send_janitor_limit_seconds": "next janitor pass",
    "publish_hub_retries": "next task",
    "publish_hub_backoff_s": "next task",
    "hf_http_timeout_s": "next task",
    "mistudio_timeout_seconds": "next request",
    "agent_request_retention_days": "next daily prune",
    "millm_steering_supported": "next request",
    "millm_base_url": "next request",
    "mistudio_base_url": "next request",
    "millm_ui_url": "next request",
    "upload_max_bytes": "next request",
    "import_confirm_bytes": "next request",
    "publish_default_namespace": "next send step",
    "dj_num_proc": "next Data-Juicer step",
    "log_level": "now",
    "operator_preview_sample_default": "next request",
    "operator_preview_sample_max": "next request",
    "operator_model_preview_sample_default": "next request",
    "operator_model_preview_sample_max": "next request",
    "label_sample_max_rows": "next request",
    "keep_share_sample_rows": "next task",
    "keep_share_max_rows": "next request",
    "generation_preview_max_prompts": "next request",
    "generation_preview_timeout_seconds": "next request",
    **dict.fromkeys(RULE_SET_CAPS, "next request"),
    **RULE_DERIVATION_KEYS,
    **BEHAVIOUR_KEYS,
    **CODE_RULE_KEYS,
}


def test_each_key_states_its_pinned_effect_timing() -> None:
    assert {k: v.effect for k, v in rs.RUNTIME_KEYS.items()} == EFFECTS


# --- the overlay ------------------------------------------------------------------------------


def _another_valid_value(key: str) -> str:
    """A value for ``key`` that differs from the environment's and that the model accepts."""
    env = getattr(env_settings(), key)
    if key == "log_level":
        return "DEBUG" if env != "DEBUG" else "WARNING"
    if key == "publish_default_namespace":
        return "some-org"
    if key in ("millm_base_url", "mistudio_base_url", "millm_ui_url"):
        return "http://sibling.test:9999"
    if isinstance(env, bool):
        return "false" if env else "true"
    if env is None:  # dj_num_proc
        return "3"
    for candidate in (env * 2, env / 2, env + 1, env - 1):
        if isinstance(env, int):
            candidate = int(candidate)
        if candidate == env:
            continue
        try:
            rs.validated_text(key, str(candidate))
        except rs.InvalidSettingValue:
            continue
        return str(candidate)
    raise AssertionError(f"no other valid value for {key}")


@pytest.mark.parametrize("key", sorted(rs.RUNTIME_KEYS))
def test_a_stored_override_reaches_get_settings(key: str) -> None:
    """Registry-derived: every runtime key's row is in force after a refresh, at the accessor
    every reader calls (012 FTID I1). Without the overlay install, every case is red."""
    text = _another_valid_value(key)
    before = getattr(get_settings(), key)
    kept = rs.apply({key: text})
    assert kept == {key: rs.validated_text(key, text)}
    after = getattr(get_settings(), key)
    assert after != before
    assert rs.text_of(after) == rs.validated_text(key, text)
    assert getattr(env_settings(), key) == before, "the environment object is never changed"
    rs.reset()
    assert getattr(get_settings(), key) == before


def test_no_override_means_the_environment_object_itself() -> None:
    rs.apply({})
    assert get_settings() is env_settings()


def test_a_row_that_no_longer_validates_is_skipped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="src.core.runtime_settings"):
        kept = rs.apply({"label_batch_poll_seconds": "-1", "upload_max_bytes": "1234"})
    assert kept == {"upload_max_bytes": "1234"}
    assert get_settings().label_batch_poll_seconds == env_settings().label_batch_poll_seconds
    assert get_settings().upload_max_bytes == 1234
    assert any("label_batch_poll_seconds" in r.getMessage() for r in caplog.records)


def test_a_row_for_a_key_outside_the_allow_list_is_ignored() -> None:
    kept = rs.apply({"database_url": "postgresql://evil/x", "hf_token": "x"})
    assert kept == {}
    assert get_settings() is env_settings()


def test_the_janitor_limits_must_exceed_twice_the_heartbeat() -> None:
    """012 FTID I4; checked on write AND when the overlay is built."""
    floor = 2 * env_settings().progress_heartbeat_seconds
    with pytest.raises(rs.InvalidSettingValue) as excinfo:
        rs.validated_text("source_import_janitor_limit_s", str(floor))
    assert "PROGRESS_HEARTBEAT_SECONDS" in excinfo.value.message
    assert rs.validated_text("source_import_janitor_limit_s", str(floor + 1)) == str(floor + 1.0)
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text("version_build_janitor_minutes", str(floor / 60))


def test_out_of_range_values_are_refused_with_the_fields_own_message() -> None:
    with pytest.raises(rs.InvalidSettingValue) as excinfo:
        rs.validated_text("millm_lease_ttl_seconds", "7201")
    assert "7200" in excinfo.value.message
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text("generation_preview_max_prompts", "6")
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text("log_level", "LOUD")
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text("millm_base_url", "http://user:pw@millm.test")
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text("mistudio_base_url", "ftp://mistudio.test")
    assert rs.validated_text("millm_base_url", "") == ""
    assert rs.validated_text("log_level", "debug") == "DEBUG"


def test_the_log_level_is_applied_at_a_refresh_and_restored_by_reset() -> None:
    root = logging.getLogger()
    before = root.level
    try:
        rs.apply({"log_level": "ERROR"})
        assert root.level == logging.ERROR
        rs.reset()
        assert logging.getLevelName(root.level) == env_settings().log_level
    finally:
        root.setLevel(before)


def test_a_refresh_with_no_log_level_change_leaves_the_root_level_alone() -> None:
    """caplog.at_level inside a task must survive the task's refresh."""
    root = logging.getLogger()
    before = root.level
    try:
        root.setLevel(logging.DEBUG)
        rs.apply({"upload_max_bytes": "99"})
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(before)


def test_refresh_if_stale_respects_its_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(rs, "refresh_from_database", lambda: calls.append(1) or True)
    rs.apply({})  # just refreshed
    assert rs.refresh_if_stale(60.0) is False and calls == []
    assert rs.refresh_if_stale(0.0) is True and calls == [1]
    rs.reset()  # never refreshed: always stale
    assert rs.refresh_if_stale(60.0) is True and calls == [1, 1]


# --- every field has a reader (D7) ----------------------------------------------------------

_SETTINGS_CALLS = {"get_settings", "env_settings", "load_settings"}

#: Fields read in a way the AST walk below cannot see, with the reason.
READER_EXEMPT: dict[str, str] = {}


def _is_settings_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    return name in _SETTINGS_CALLS


def settings_reads(root: Path) -> dict[str, set[str]]:
    """Attribute reads on a ``get_settings()``/``env_settings()`` result, directly or through a
    name assigned from one in the same module. ``core/config.py`` (the declarations) is skipped."""
    reads: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        if path == root / "core" / "config.py":
            continue
        tree = ast.parse(path.read_text())
        aliases = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and _is_settings_call(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if _is_settings_call(value) or (isinstance(value, ast.Name) and value.id in aliases):
                reads.setdefault(node.attr, set()).add(str(path.relative_to(root)))
    return reads


def test_every_settings_field_has_a_reader() -> None:
    """ENH-003 D7: a field nothing reads is deleted (with its .env.example line), not kept."""
    reads = settings_reads(SRC)
    unread = sorted(set(Settings.model_fields) - set(reads) - set(READER_EXEMPT))
    assert unread == [], f"Settings fields with no reader in src/: {unread}"


def test_the_reader_scan_sees_both_read_shapes(tmp_path: Path) -> None:
    """Negative control: the scan must find a direct read and an aliased read, and must NOT count
    a read of an unrelated object (so it cannot pass by matching nothing or everything)."""
    pkg = tmp_path / "src"
    (pkg / "core").mkdir(parents=True)
    (pkg / "a.py").write_text(
        "from x import get_settings\n"
        "def f():\n    return get_settings().alpha\n"
        "def g():\n    s = get_settings()\n    return s.beta\n"
        "def h(other):\n    return other.gamma\n"
    )
    reads = settings_reads(pkg)
    assert set(reads) == {"alpha", "beta"}


def test_every_mcp_setting_has_a_reader() -> None:
    mcp_root = SRC / "mcp_server"
    attrs: set[str] = set()
    for path in mcp_root.rglob("*.py"):
        if path.name == "config.py":
            continue
        attrs |= {
            n.attr for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Attribute)
        }
    config_tree = ast.parse((mcp_root / "config.py").read_text())
    # requested_categories() and api_url live in config.py and read these two there.
    in_config = {
        n.attr
        for n in ast.walk(config_tree)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    }
    unread = sorted(set(MCPSettings.model_fields) - attrs - in_config)
    assert unread == [], unread


def _model_dump_round_trips() -> Any:
    return Settings.model_validate(env_settings().model_dump())


def test_the_environment_round_trips_through_validation() -> None:
    """The overlay is built by re-validating a dump of the environment object; secrets and paths
    must survive that (or every override would fail to apply)."""
    rebuilt = _model_dump_round_trips()
    env = env_settings()
    assert rebuilt.settings_encryption_key.get_secret_value() == (
        env.settings_encryption_key.get_secret_value()
    )
    assert rebuilt.data_dir == env.data_dir


# --- feature 018: the runner limits never go below the contract floor (FTASKS 10.11, M30) ------


@pytest.mark.parametrize("key", sorted(FLOORED))
def test_a_runner_limit_below_the_contract_floor_is_refused_at_write_and_at_start(
    key: str,
) -> None:
    import json

    from pydantic import ValidationError

    from src.runner_contract.document import PACKAGED_CONTRACT_PATH

    floor = json.loads(PACKAGED_CONTRACT_PATH.read_text(encoding="utf-8"))["limits_floor"][
        FLOORED[key]
    ]
    assert getattr(env_settings(), key) == floor, "the default is the published floor"
    assert rs.validated_text(key, str(floor)) == str(floor)
    assert rs.validated_text(key, str(floor * 2)) == str(floor * 2)
    with pytest.raises(rs.InvalidSettingValue):
        rs.validated_text(key, str(floor - 1))  # a runtime write
    with pytest.raises(ValidationError):
        Settings(**{key: floor - 1})  # API start with the environment (or a stored row) below it


def test_code_rules_are_off_unless_the_operator_turns_them_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """018 Review R2; ENH-006 D62 (code rules stay OFF until 018 S5's approval card shows the
    source, the case results and the revoked state): off by default, and no manifest anywhere
    under k8s/ sets CODE_RULES_ENABLED, so the environment as deployed builds Settings with code
    rules switched off. Only the operator turns it on (agent_forbidden)."""
    import yaml

    monkeypatch.delenv("CODE_RULES_ENABLED", raising=False)
    assert Settings().code_rules_enabled is False
    assert Settings.model_fields["code_rules_enabled"].default is False
    config = Path(__file__).resolve().parents[3] / "k8s" / "base" / "config.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))["data"]
    assert "CODE_RULES_ENABLED" not in data
    k8s = Path(__file__).resolve().parents[3] / "k8s"
    setting = [p for p in sorted(k8s.rglob("*.yaml")) if "CODE_RULES_ENABLED" in p.read_text()]
    assert setting == [], setting
    assert rs.RUNTIME_KEYS["code_rules_enabled"].agent_forbidden is True
