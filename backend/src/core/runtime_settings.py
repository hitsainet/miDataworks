# Origin: miForge (Onegaishimas/miForge) backend/src/core/runtime_settings.py @ 655c731
# Mode: adapt (docs/REUSE.md). Kept: the environment stays the source of truth, an override is a
# row and "use the environment value" deletes it; values validated by building Settings; a row
# that does not validate is ignored and logged; refresh after a write, at API startup and in
# Celery task_prerun. Changed: the overlay is installed UNDER get_settings() so every reader is
# live (012 FTID I1); overrides live in the existing dw_app_settings registry (ENH-003 D10); the
# allow-list carries label, effect timing and agent rules; long runs refresh at their boundaries.
"""Runtime overrides of the allow-listed environment settings (feature 012; ENH-003 §3.3).

**The value in force is ``get_settings()``.** It returns the environment ``Settings`` with this
process's validated overlay on top (``core.config.install_overlay``). No reader opens a database
connection: the overlay is filled only at the refresh points below, each pinned by a test that
fails when it is removed (012 FTASKS 3.0):

* the settings routes, right after a write commits (``refresh_async``);
* the API's ``lifespan`` start (``refresh_async``);
* Celery ``task_prerun`` (``on_task_prerun``, connected in ``core.celery_app``), so every task
  starts with the overrides as stored, in the worker's own process;
* the chunk boundaries and poll loops of long runs (``refresh_if_stale``), so a change reaches a
  run that has been going for hours (012 FTID I6).

Each refresh also applies the log level (012 FTID I7).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .config import LOG_LEVELS, Settings, env_settings, get_settings, install_overlay

logger = logging.getLogger(__name__)

Effect = Literal[
    "now",
    "next request",
    "next task",
    "next run",
    "next poll",
    "next janitor pass",
    "next check in a running run",
    "next lease grant or renewal",
    "next daily prune",
    "next send step",
    "next Data-Juicer step",
]


@dataclass(frozen=True)
class RuntimeKey:
    """One setting the operator may change while miDataworks runs."""

    key: str
    label: str
    effect: Effect
    #: Where the change lands, in plain words, when ``effect`` alone does not say it.
    effect_detail: str = ""
    #: Agents may not change it at all, with or without approval (ENH-003 §3.3: operator-only).
    agent_forbidden: bool = False
    #: Shown beside the editor: what to know before changing it.
    caution: str = ""


_SECONDS = "seconds"

#: The allow-list (ENH-003 D2: §3.3's ~26 keys plus the preview and sample limit group). Only these
#: keys are writable; a row for any other key is ignored.
RUNTIME_KEYS: dict[str, RuntimeKey] = {
    spec.key: spec
    for spec in (
        # -- labeling and generation runs
        RuntimeKey(
            "endpoint_http_timeout_seconds",
            "Endpoint request time limit, in seconds",
            "next run",
            "Runs started or resumed after the change.",
        ),
        RuntimeKey(
            "label_max_consecutive_failures",
            "Failed rows in a row before a run stops",
            "next check in a running run",
            "Running label and generation runs pick it up at their next chunk (within 10 seconds).",
        ),
        RuntimeKey(
            "label_transient_retries",
            "Retries of a temporary endpoint error",
            "next run",
            "Runs started or resumed after the change.",
        ),
        RuntimeKey(
            "label_backoff_cap_seconds",
            "Longest wait when the endpoint is busy, in seconds",
            "next run",
            "Runs started or resumed after the change.",
        ),
        RuntimeKey(
            "millm_lease_ttl_seconds",
            "miLLM model lease lifetime, in seconds",
            "next lease grant or renewal",
        ),
        RuntimeKey(
            "label_batch_poll_seconds",
            "How often a batch label run checks its miLLM batch, in seconds",
            "next poll",
        ),
        # -- detector sets
        RuntimeKey(
            "detector_send_poll_seconds",
            "How often a detector-set send checks miStudio, in seconds",
            "next poll",
        ),
        RuntimeKey(
            "detector_send_download_timeout_minutes",
            "How long a send waits for miStudio's download, in minutes",
            "next send step",
        ),
        # -- the janitor (each must exceed twice PROGRESS_HEARTBEAT_SECONDS; config.py)
        RuntimeKey(
            "version_build_janitor_minutes",
            "Silence before a version build is marked failed, in minutes",
            "next janitor pass",
        ),
        RuntimeKey(
            "source_import_janitor_limit_s",
            "Silence before an import is marked failed, in seconds",
            "next janitor pass",
        ),
        RuntimeKey(
            "publish_janitor_limit_seconds",
            "Silence before a publish or export job is marked failed, in seconds",
            "next janitor pass",
        ),
        RuntimeKey(
            "detector_send_janitor_limit_seconds",
            "Silence before a detector-set send is marked failed, in seconds",
            "next janitor pass",
        ),
        # -- the Hub and miStudio clients
        RuntimeKey("publish_hub_retries", "Retries of a busy or failing Hub call", "next task"),
        RuntimeKey(
            "publish_hub_backoff_s",
            "First wait before retrying a Hub call, in seconds",
            "next task",
        ),
        RuntimeKey("hf_http_timeout_s", "Hugging Face request time limit, in seconds", "next task"),
        RuntimeKey(
            "mistudio_timeout_seconds",
            "miStudio request time limit, in seconds",
            "next request",
            "Requests and send steps started after the change.",
        ),
        # -- retention
        RuntimeKey(
            "agent_request_retention_days",
            "Days agent activity is kept",
            "next daily prune",
        ),
        # -- operator-only switches and sibling addresses
        RuntimeKey(
            "millm_steering_supported",
            "Allow steered generation",
            "next request",
            agent_forbidden=True,
            caution="Off refuses steered generation even when miLLM serves it.",
        ),
        RuntimeKey(
            "millm_base_url",
            "miLLM address",
            "next request",
            "Requests and tasks started after the change. Empty means not configured.",
            agent_forbidden=True,
        ),
        RuntimeKey(
            "mistudio_base_url",
            "miStudio address",
            "next request",
            "Requests and tasks started after the change. Empty means not configured.",
            agent_forbidden=True,
            caution=(
                "Reward marks and probe links are stored against the address they were made with; "
                "after a change, the ones made with the old address are not shown."
            ),
        ),
        RuntimeKey(
            "millm_ui_url",
            "miLLM admin screen address (as your browser reaches it)",
            "next request",
            agent_forbidden=True,
        ),
        # -- storage and publishing (already database-backed before 012)
        RuntimeKey("upload_max_bytes", "Largest upload, in bytes", "next request"),
        RuntimeKey(
            "import_confirm_bytes",
            "Imports above this many bytes need a confirmation",
            "next request",
            "Imports checked after the change.",
        ),
        RuntimeKey(
            "publish_default_namespace",
            "Hugging Face namespace a detector-set send publishes to",
            "next send step",
            "Empty: the send form asks for one.",
        ),
        # -- Data-Juicer
        RuntimeKey(
            "dj_num_proc",
            "Data-Juicer processes per step (empty: the container's processor limit)",
            "next Data-Juicer step",
            caution="Keep it at or under the Data-Juicer container's processor limit.",
        ),
        # -- logging
        RuntimeKey(
            "log_level",
            "Log detail",
            "now",
            "The web server at once; each worker at its next task.",
        ),
        # -- preview and sample limits (keep the code's upper bounds)
        RuntimeKey(
            "operator_preview_sample_default",
            "Rows an operator preview samples by default",
            "next request",
        ),
        RuntimeKey(
            "operator_preview_sample_max",
            "Most rows an operator preview may sample",
            "next request",
        ),
        RuntimeKey(
            "operator_model_preview_sample_default",
            "Rows a model-calling operator preview samples by default",
            "next request",
        ),
        RuntimeKey(
            "operator_model_preview_sample_max",
            "Most rows a model-calling operator preview may sample",
            "next request",
        ),
        RuntimeKey(
            "label_sample_max_rows",
            'Most rows "Try it on a sample" labels',
            "next request",
        ),
        RuntimeKey(
            "keep_share_sample_rows",
            "Rows a keep-share estimate scores",
            "next task",
        ),
        RuntimeKey(
            "keep_share_max_rows",
            "Largest keep-share preview sample",
            "next request",
        ),
        RuntimeKey(
            "generation_preview_max_prompts",
            "Most prompts a generation preview writes (at most 5)",
            "next request",
        ),
        RuntimeKey(
            "generation_preview_timeout_seconds",
            "Generation preview time limit, in seconds (at most 60)",
            "next request",
        ),
        # -- rule signals (feature 016, FR-016.37): caps a save, validate, preview or clone checks
        RuntimeKey("rule_set_max_signals", "Most signals in one rule set", "next request"),
        RuntimeKey("rule_set_max_cases", "Most cases in one rule set", "next request"),
        RuntimeKey(
            "rule_set_max_terms", "Most terms in one lexical-weights signal", "next request"
        ),
        RuntimeKey(
            "rule_set_max_code_params_bytes",
            "Largest code-rule parameters, in bytes",
            "next request",
        ),
        RuntimeKey(
            "rule_set_max_document_bytes",
            "Largest rule-set document, in bytes",
            "next request",
            caution="A stored rule set larger than a lowered limit still builds; only new saves "
            "are refused.",
        ),
        RuntimeKey(
            "rule_preview_max_text_bytes",
            "Largest text a rule-set preview reads, in bytes",
            "next request",
        ),
        RuntimeKey(
            "recipe_max_rule_sets", "Most rule-set versions one recipe pins", "next request"
        ),
        RuntimeKey(
            "behaviour_max_held_out_values",
            "Most held-out values per directing column of a split",
            "next request",
            "Recipes validated and builds started after the change; a split step that names "
            "more is refused naming this setting.",
        ),
        RuntimeKey(
            "generation_requests_in_flight",
            "Generation requests a carrier run keeps in flight",
            "next run",
            "A carrier run on the native path sends this many plan rows' requests at once, "
            "recorded on the run when it starts; other runs send one at a time. 1 until a "
            "measured run shows more is faster on the lease holder.",
        ),
        RuntimeKey(
            "behaviour_max_vocabulary_values",
            "Most values one field of a behaviour's vocabulary declares",
            "next request",
            "Recipes validated and builds started after the change; a carrier plan whose "
            "vocabulary declares more in one field is refused naming this setting.",
        ),
        RuntimeKey(
            "rule_derivation_max_signals",
            "Most literals a rule derivation proposes",
            "next request",
        ),
        RuntimeKey(
            "rule_derivation_min_held_out_per_class",
            "Held-out rows per class before a derivation is measured",
            "next run",
            "Derivations started after the change (a started one keeps its value).",
        ),
        RuntimeKey(
            "rule_derivation_max_source_rows",
            "Most source rows a rule derivation reads",
            "next run",
            "Derivations started after the change (a started one keeps its value).",
        ),
        RuntimeKey(
            "rule_derivation_time_limit_s",
            "Rule derivation time limit, in seconds",
            "next run",
            "Applied when a derivation is sent to a worker.",
        ),
    )
}

#: Changeable only by restarting the backend pod, which stops every running job. Shown read-only.
RESTART_POD = "midataworks-backend"
RESTART_KEYS: dict[str, str] = {
    "janitor_interval_seconds": "Beat's schedule is fixed when it starts.",
    "curation_sweep_interval_s": "Beat's schedule is fixed when it starts.",
    "diversity_sweep_seconds": "Beat's schedule is fixed when it starts.",
    "operator_preview_hard_limit_s": "The preview worker's task time limit is fixed when it starts.",
}

#: The approval rules: deployment configuration, read-only here (P-08 kept; ENH-003 D11).
APPROVAL_RULE_KEYS: tuple[str, ...] = (
    "agent_label_row_threshold",
    "agent_label_window_hours",
    "approval_ttl_hours",
    "detector_length_overlap_min",
    "judge_parse_failure_stop_share",
    "judge_parse_failure_min_rows",
)

#: Never writable here, tested by name: infrastructure wiring, secrets, volumes, test switches,
#: CI and contract pins, artifact-byte knobs whose effect on content hashes is unverified, and
#: identities recorded elsewhere (ENH-003 §3.3 "Excluded"). Moving one into RUNTIME_KEYS turns
#: ``test_never_writable_keys_are_refused_by_name`` red.
NEVER_WRITABLE: frozenset[str] = frozenset(
    {
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
        "operator_name",
        # 014: the PIN's recovery path must never be switchable from the app it unlocks.
        "settings_pin_bypass",
    }
)

#: Shown only as "set" or "not set".
SECRET_KEYS: frozenset[str] = frozenset(
    {"settings_encryption_key", "internal_api_secret", "designer_handoff_key"}
)

#: Shown with any password removed.
URL_KEYS: frozenset[str] = frozenset({"database_url", "redis_url"})


# --------------------------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------------------------


def text_of(value: Any) -> str:
    """A validated value as stored text: what ``Settings`` parses back to the same value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class InvalidSettingValue(ValueError):
    """The value is refused by the ``Settings`` model; ``message`` is the field's own reason."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key
        self.message = message


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error.get("loc", ())) or "value"
        msg = str(error.get("msg", "invalid"))
        parts.append(f"{loc.upper()}: {msg}" if loc != "value" else msg)
    return "; ".join(parts)


def _candidate(base: dict[str, Any], key: str, raw: Any) -> Settings:
    data = dict(base)
    data[key] = raw
    return Settings.model_validate(data)


def validated_text(key: str, raw: Any, *, others: dict[str, str] | None = None) -> str:
    """``raw`` for ``key`` as ``Settings`` would hold it, as stored text; or ``InvalidSettingValue``.

    Validated by building the whole model with every OTHER override in force, so cross-field
    rules (each janitor limit against the heartbeat) see what the processes will see.
    """
    if key not in RUNTIME_KEYS:
        raise InvalidSettingValue(key, f"{key} cannot be changed while miDataworks runs.")
    base = env_settings().model_dump()
    for other, text in (others or {}).items():
        if other != key and other in RUNTIME_KEYS:
            base[other] = text
    try:
        built = _candidate(base, key, raw)
    except ValidationError as exc:
        raise InvalidSettingValue(key, _validation_message(exc)) from None
    return text_of(getattr(built, key))


# --------------------------------------------------------------------------------------------
# The overlay
# --------------------------------------------------------------------------------------------

_lock = threading.Lock()
_overrides: dict[str, str] = {}
_refreshed_at: float | None = None


def overrides() -> dict[str, str]:
    """A copy of the overrides (key -> stored text) in force in this process."""
    with _lock:
        return dict(_overrides)


def apply(rows: dict[str, str]) -> dict[str, str]:
    """Rebuild the overlay from stored rows (key -> text); returns the overrides kept.

    Runtime keys only, in key order, each validated with the ones kept before it; a row that does
    not validate is skipped and logged (never applied). No override kept: the overlay is removed
    and ``get_settings()`` is the environment object itself.
    """
    global _refreshed_at
    env = env_settings()
    data = env.model_dump()
    kept: dict[str, str] = {}
    built: Settings | None = None
    for key in sorted(rows):
        if key not in RUNTIME_KEYS:
            continue  # the registry's own keys (hf_token, operator_name) share the table
        try:
            candidate = _candidate(data, key, rows[key])
        except ValidationError as exc:
            logger.warning(
                "Ignoring the stored override of %s: %s",
                key,
                _validation_message(exc),
                extra={"code": "setting_override_invalid", "key": key},
            )
            continue
        data[key] = getattr(candidate, key)
        kept[key] = text_of(data[key])
        built = candidate
    with _lock:
        _overrides.clear()
        _overrides.update(kept)
        _refreshed_at = time.monotonic()
        install_overlay(built if kept else None)
    apply_log_level()
    return dict(kept)


#: The level this module last set on the root logger; None means the environment's level, which
#: ``configure_logging`` set at process start.
_applied_level: str | None = None


def apply_log_level() -> None:
    """Set the root logger to the level in force when it differs from the last one set (I7).

    Only a CHANGE is applied, so a process with no log-level override never has its root level
    touched by a refresh (and a test's ``caplog.at_level`` survives a task's refresh).
    """
    global _applied_level
    level = get_settings().log_level
    last = _applied_level or env_settings().log_level
    if level != last and level in LOG_LEVELS:
        logging.getLogger().setLevel(level)
    _applied_level = level


def _rows_sync(session: Any) -> dict[str, str]:
    from sqlalchemy import select

    from ..models.app_setting import AppSetting

    found = session.execute(
        select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(list(RUNTIME_KEYS)))
    ).all()
    return {str(k): str(v) for k, v in found}


def refresh_sync(session: Any) -> dict[str, str]:
    """Read the stored overrides through a sync session and rebuild the overlay."""
    return apply(_rows_sync(session))


async def refresh_async(db: Any) -> dict[str, str]:
    """Read the stored overrides through an async session and rebuild the overlay."""
    from sqlalchemy import select

    from ..models.app_setting import AppSetting

    found = (
        await db.execute(
            select(AppSetting.key, AppSetting.value).where(AppSetting.key.in_(list(RUNTIME_KEYS)))
        )
    ).all()
    return apply({str(k): str(v) for k, v in found})


def refresh_from_database() -> bool:
    """``refresh_sync`` through a session of its own. False (logged) if the read failed.

    A failure keeps the previous overlay and never fails the task or the startup: the environment
    values still apply, which is what an absent override means.
    """
    try:
        from .database import get_sync_engine, sync_session_factory

        get_sync_engine()
        with sync_session_factory()() as session:
            refresh_sync(session)
    except Exception as exc:  # noqa: BLE001 - a settings read must never fail the caller
        logger.warning(
            "Could not read the runtime setting overrides (%s); the values in force are kept",
            type(exc).__name__,
            extra={"code": "setting_overrides_unreadable"},
        )
        return False
    return True


#: The staleness bound of a long run's boundary refresh (012 FTID I6).
BOUNDARY_MAX_AGE_S = 10.0


def refresh_if_stale(max_age: float | None = None) -> bool:
    """Refresh when the overlay is older than ``max_age`` seconds (default
    ``BOUNDARY_MAX_AGE_S``). True when it refreshed."""
    bound = BOUNDARY_MAX_AGE_S if max_age is None else max_age
    with _lock:
        last = _refreshed_at
    if last is not None and time.monotonic() - last < bound:
        return False
    return refresh_from_database()


def on_task_prerun(**_: Any) -> None:
    """Celery ``task_prerun``: every task starts with the overrides as stored."""
    refresh_from_database()


def reset() -> None:
    """Remove the overlay (tests; and a process that wants the environment back)."""
    global _refreshed_at
    with _lock:
        _overrides.clear()
        _refreshed_at = None
        install_overlay(None)
    apply_log_level()


def env_value(key: str) -> Any:
    return getattr(env_settings(), key)


def value_in_force(key: str) -> Any:
    return getattr(get_settings(), key)
