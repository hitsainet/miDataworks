"""Configuration from the environment (ADR-002, PADR section 2.5; Foundation task 3.1).

What this module guarantees:
- Every variable the backend reads is declared here, and ``.env.example`` lists each one
  (``tests/unit/test_config.py`` compares the two).
- A missing required variable fails at start, naming the variable. Nothing is guessed: there is
  no fallback encryption key and no fallback database.
- Booleans fall back to their declared default when absent. pydantic rejects an unparsable
  value loudly rather than reading it as False.

What it refuses: an encryption key shorter than 32 characters, and a ``DATA_DIR`` that is not an
absolute path.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Variables that must be set; there is no safe default for any of them.
REQUIRED_VARIABLES: tuple[str, ...] = (
    "DATABASE_URL",
    "REDIS_URL",
    "SETTINGS_ENCRYPTION_KEY",
    "INTERNAL_API_SECRET",
)


class Settings(BaseSettings):
    """The backend's configuration. Field names map to upper-case environment variables."""

    model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    # --- required ---------------------------------------------------------------------------
    database_url: str = Field(
        ..., description="Async SQLAlchemy URL, postgresql+asyncpg://user:pass@host:port/db"
    )
    redis_url: str = Field(..., description="Redis URL; Celery broker and result backend")
    settings_encryption_key: SecretStr = Field(
        ..., description="Key material for AES-256-GCM settings encryption (HKDF-derived)"
    )
    internal_api_secret: SecretStr = Field(
        ..., description="Shared secret for the worker-to-API emit route (never exposed)"
    )

    # --- optional with defaults ---------------------------------------------------------------
    environment: str = Field("development", description="development | test | production")
    log_level: str = Field("INFO", description="Root log level")
    data_dir: Path = Field(Path("/data/dataworks"), description="Data volume root (ADR-004)")
    # Both siblings are OPTIONAL (operator principle, 2026-10-07): miDataworks is a standalone
    # dataset tool, and every core flow runs with both unset. Unset, health reports the sibling as
    # "not configured" and only the integration-only actions refuse, naming it.
    millm_base_url: str | None = Field(
        None,
        description="miLLM base URL (optional): health, probe-verdict and feature-tag label runs. "
        "Endpoint roles are configured separately and may point at any OpenAI-compatible or TEI "
        "server; miLLM-only behaviour there (lease, batch, steering) is detected at runtime.",
    )
    mistudio_base_url: str | None = Field(
        None,
        description="miStudio base URL (optional): health, and 009's detector-set send, results "
        "refresh and reward marks. Unset, those refuse with mistudio_not_configured.",
    )
    dataworks_api_url: str = Field(
        "http://localhost:8000", description="Where workers reach the API's internal emit route"
    )
    progress_heartbeat_seconds: float = Field(
        60.0, gt=0, description="record_progress time throttle (ADR-007)"
    )
    health_probe_timeout_seconds: float = Field(
        2.0, gt=0, description="Timeout for each dependency probe in GET /api/health"
    )
    approval_ttl_hours: float = Field(24.0, gt=0, description="Pending approvals expire (P-08)")
    agent_label_row_threshold: int = Field(
        5000, ge=0, description="Agent label rows per version per window before approval (P-07)"
    )
    agent_label_window_hours: float = Field(
        24.0, gt=0, description="Length of the P-07 agent labelling window (010 FTDD section 5.4)"
    )
    # --- feature 010: the Agent access card (FTID 010 section 9) --------------------------------
    mcp_public_url: str | None = Field(
        None, description="The MCP server's address, shown on the Agent access card"
    )
    mcp_internal_url: str | None = Field(
        None, description="Where the API reads the MCP server's /health for the card"
    )
    agent_request_retention_days: int = Field(
        30, gt=0, description="dw_agent_requests rows older than this are pruned (010 FTDD 4.2)"
    )
    janitor_interval_seconds: float = Field(
        120.0, gt=0, description="Beat interval for the job janitor"
    )
    # --- feature 002: versions, recipes and provenance (FTDD 002 section 11) -------------------
    version_build_janitor_minutes: float = Field(
        30.0, gt=0, description="Janitor limit for version_build and version_verify jobs"
    )
    row_event_copy_chunk: int = Field(
        50_000, gt=0, description="Row events per COPY chunk at step ingestion"
    )
    parquet_row_group_rows: int = Field(
        100_000, gt=0, description="Rows per Parquet row group in version and step files"
    )
    row_history_text_matches: int = Field(
        50, gt=0, description="Most row keys a text search resolves to in row history"
    )
    orphan_sweep_age_minutes: float = Field(
        60.0, gt=0, description="Version directories with no row older than this are swept"
    )
    # --- feature 001: sources and import (001 FTDD section 11) ----------------------------------
    hf_hub_url: str = Field("https://huggingface.co", description="Hugging Face Hub API base URL")
    hf_datasets_server_url: str = Field(
        "https://datasets-server.huggingface.co", description="Hugging Face Dataset Viewer URL"
    )
    hf_http_timeout_s: float = Field(30.0, gt=0, description="Timeout for each Hub or Viewer call")
    preview_timeout_s: float = Field(30.0, gt=0, description="How long the API waits for a preview")
    ephemeral_secret_ttl_s: int = Field(
        900, gt=0, description="Lifetime of a per-import token in the ephemeral store"
    )
    source_import_janitor_limit_s: float = Field(
        900.0, gt=0, description="Janitor heartbeat limit for source_import jobs"
    )
    upload_max_bytes: int = Field(
        2 * 1024**3, gt=0, description="Default upload size cap per file (app setting overrides)"
    )
    import_confirm_bytes: int = Field(
        50 * 1000**3, gt=0, description="Imports larger than this need confirm_large (T-02)"
    )
    # --- feature 008: publishing, export and handoff contract (FTDD 008 section 11) ----------
    publish_default_namespace: str = Field(
        "", description="Hub namespace the publish form suggests; empty means the operator types it"
    )
    publish_max_file_bytes: int = Field(
        200 * 1000**3,
        gt=0,
        description=(
            "Largest split file a publish uploads (FR-008.65). Default: the Hub's recommended "
            "per-file size (<200GB); its hard limit is 500GB (huggingface.co/docs/hub/"
            "storage-limits, read 2026-10-07)"
        ),
    )
    publish_hub_retries: int = Field(
        5, ge=0, description="Retries for a Hub call answered 429 or 5xx (EC-5)"
    )
    publish_hub_backoff_s: float = Field(
        2.0, ge=0, description="First retry wait when the Hub sends no Retry-After"
    )
    # --- feature 006 (FTID 006 section 9) ---
    calibration_max_per_rater_rows: int = Field(
        200_000, gt=0, description="Refuse larger per-rater calibration sets until measured"
    )
    review_candidate_max_bytes: int = Field(
        65_536, gt=0, description="Largest external review candidate payload, in bytes"
    )
    publish_janitor_limit_seconds: float = Field(
        1800.0, gt=0, description="Janitor heartbeat limit for the five publish job kinds"
    )
    publish_build_batch_rows: int = Field(
        65_536, gt=0, description="Rows per streamed batch when writing publish split files"
    )
    reward_bundle_trl_version: str = Field(
        "1.14.1", description="TRL version a reward bundle is written for"
    )
    app_build: str = Field(
        "dev", description="Image commit recorded as producer.build in every manifest"
    )
    celery_task_always_eager: bool = Field(
        False, description="Run tasks in-process (tests only); never set in a deployment"
    )
    # --- feature 009: detector sets (FTID 009 section 9) -----------------------------------
    detector_send_poll_seconds: float = Field(
        10.0, gt=0, description="Seconds between polls of a miStudio download during a send"
    )
    detector_send_download_timeout_minutes: float = Field(
        60.0, gt=0, description="A miStudio download not ready after this long fails the step"
    )
    detector_send_janitor_limit_seconds: float = Field(
        900.0, gt=0, description="Janitor heartbeat limit for a detector-set send (mistudio_send)"
    )
    detector_length_overlap_min: float | None = Field(
        0.5,
        ge=0,
        le=1,
        description=(
            "D-5 tolerance for the calibration-length quantile overlap; measured 2026-10-07 at "
            "0.000 (chat negatives) and 0.777 (headline negatives) against Humicroedit (T-44)"
        ),
    )
    detector_capability_ttl_seconds: float = Field(
        600.0, gt=0, description="How long a read of miStudio's served OpenAPI is trusted"
    )
    mistudio_timeout_seconds: float = Field(
        30.0, gt=0, description="Timeout of each call to miStudio (FTDD 009 section 5.4)"
    )
    # --- feature 003: operators (FTDD 003 section 11.2) ---------------------------------------
    operator_test_fixtures: bool = Field(
        False, description="Register the test-only fixture operators (tests only; never deployed)"
    )
    operator_preview_sample_default: int = Field(
        300, gt=0, description="Rows sampled for an operator preview when none is asked for"
    )
    operator_preview_sample_max: int = Field(
        2000, gt=0, description="Largest preview sample for an operator that calls no model"
    )
    operator_model_preview_sample_default: int = Field(
        5, gt=0, description="Preview sample for a model-calling operator (FR-005.20)"
    )
    operator_model_preview_sample_max: int = Field(
        50, gt=0, description="Largest preview sample for a model-calling operator"
    )
    operator_batch_rows: int = Field(
        10_000, gt=0, description="Rows per record batch a step streams to an operator"
    )
    operator_preview_sync_wait_s: float = Field(
        3.0, ge=0, description="How long the preview route waits before answering 202"
    )
    operator_preview_soft_limit_s: float = Field(
        60.0, gt=0, description="Soft time limit of one preview task (preview_timeout)"
    )
    operator_preview_hard_limit_s: float = Field(
        90.0, gt=0, description="Hard time limit of one preview task"
    )
    operator_preview_result_ttl_s: int = Field(
        3600, gt=0, description="How long a preview result stays in Redis"
    )
    operator_params_max_bytes: int = Field(
        65_536, gt=0, description="Largest accepted operator params body, in bytes"
    )
    # --- feature 004: curation (FTDD 004 section 11.2). The warning MARGIN is not here: it is
    # data with history (dw_shortcut_levels) and a code default (P-19).
    curation_inline_max_rows: int = Field(
        20_000, gt=0, description="Reports on inputs up to this many rows run inline, not as jobs"
    )
    curation_audit_folds: int = Field(
        5, ge=2, le=20, description="Stratified folds for the held-out shortcut figure"
    )
    curation_control_runs: int = Field(
        5, ge=1, le=50, description="Permuted-label runs averaged into the audit's control"
    )
    curation_control_tolerance_pp: float = Field(
        2.0,
        ge=0,
        lt=50,
        description="Points above chance at which a control marks a column invalid",
    )
    curation_minhash_permutations: int = Field(
        128, ge=16, le=1024, description="MinHash permutations for near-dedup and leakage"
    )
    curation_embed_exact_max_rows: int = Field(
        200_000, gt=0, description="Largest input for blocked exact-cosine embedding dedup"
    )
    curation_sweep_interval_s: float = Field(
        60.0, gt=0, description="How often Beat looks for completed versions without an audit"
    )
    curation_cell_sample_size: int = Field(
        30, gt=0, le=500, description="Seeded sample rows shown per audit cell"
    )
    datajuicer_health_cache_s: float = Field(
        30.0, ge=0, description="How long the Data-Juicer worker liveness answer is cached"
    )
    designer_handoff_key: SecretStr | None = Field(
        None,
        description="Shared with the Data Designer worker only: seals a model key handed to it",
    )
    dj_num_proc: int | None = Field(
        None, gt=0, description="Data-Juicer num_proc; unset means the container's CPU limit"
    )

    # --- feature 005: labeling and endpoints (FTDD 005 section 11) ------------------------------
    # The P-07 threshold is AGENT_LABEL_ROW_THRESHOLD above and the window is the shared ledger's
    # 24 h (services/agent_label_ledger.py): one budget for builds and label runs (FR-002.50).
    label_chunk_size: int = Field(200, gt=0, description="Rows per committed label chunk")
    label_max_consecutive_failures: int = Field(
        20, gt=0, description="Consecutive non-backpressure row failures before a run fails"
    )
    label_backoff_cap_seconds: float = Field(
        60.0, gt=0, description="Cap of the 503 backoff when no Retry-After is sent (FR-005.46)"
    )
    label_transient_retries: int = Field(
        3, ge=0, description="Retries of a transient 5xx or connection error before it counts"
    )
    judge_parse_failure_stop_share: float = Field(
        0.05, gt=0, le=1, description="Judge runs stop above this parse-failure share (T-23)"
    )
    judge_parse_failure_min_rows: int = Field(
        200, gt=0, description="Judged rows before the parse-failure share is enforced (T-23)"
    )
    keep_share_sample_rows: int = Field(
        400, gt=0, description="Rows a keep-share estimate scores (FR-005.19)"
    )
    keep_share_max_rows: int = Field(1000, gt=0, description="Largest keep-share preview sample")
    label_sample_max_rows: int = Field(
        20, gt=0, description="Most rows 'Try it on a sample' scores (FR-005.20)"
    )
    label_sample_timeout_seconds: float = Field(
        30.0, gt=0, description="Time limit of one 'Try it on a sample' request"
    )
    endpoint_http_timeout_seconds: float = Field(
        120.0, gt=0, description="Timeout of one request to a classifier or judge endpoint"
    )
    millm_lease_ttl_seconds: int = Field(
        1800, ge=1, le=7200, description="TTL of the shared miLLM model lease (FTDD 005 6.5)"
    )
    millm_lease_holder: str = Field(
        "midataworks", min_length=1, max_length=128, description="Holder name on miLLM leases"
    )
    label_batch_max_lines: int = Field(
        50_000, gt=0, le=50_000, description="Rows per miLLM batch (miLLM's limit is 50,000)"
    )
    label_batch_poll_seconds: float = Field(
        5.0, gt=0, description="How often a batch-transport run polls its batch"
    )
    label_lease_retry_seconds: float = Field(
        60.0, gt=0, description="Wait before a run blocked by another lease holder retries"
    )

    # --- feature 007: synthetic generation and steered pairs (FTDD 007 section 11) ---------------
    generation_chunk_size: int = Field(
        200, gt=0, le=10_000, description="Prompts per committed generation chunk"
    )
    generation_preview_max_prompts: int = Field(
        5, gt=0, le=5, description="Most prompts 'Try on a sample' generates (FR-007.44)"
    )
    generation_preview_timeout_seconds: float = Field(
        60.0, gt=0, le=60, description="Time limit of one generation preview"
    )
    generation_max_n_responses: int = Field(
        16, ge=1, le=16, description="Upper bound on responses per prompt (N)"
    )
    #: 003's spike 1.4 (2026-10-07) found Data Designer sends extra_body statically and cannot run
    #: in this image, so the default is the native path; ``relay`` runs 007's own operator on the
    #: loopback relay (FTASKS 1.5).
    generation_engine_path: Literal["relay", "native"] = Field(
        "native", description="How generation calls reach the endpoint: native or relay"
    )
    #: True only after the ADR-027 search finds miLLM serving X-miLLM-Steering and inline
    #: steering (FTASKS 1.2: present at miLLM 44e4c4a, 2026-10-07).
    millm_steering_supported: bool = Field(
        True, description="miLLM serves inline steering and X-miLLM-Steering (feature 028)"
    )
    diversity_sample_size: int = Field(
        5000, gt=0, le=100_000, description="Rows per side a diversity report samples"
    )
    diversity_bootstrap_resamples: int = Field(
        1000, ge=100, le=10_000, description="Bootstrap resamples per diversity interval"
    )
    diversity_sweep_seconds: float = Field(
        300.0, gt=0, description="How often Beat queues missing diversity reports"
    )
    diversity_cluster_k: int = Field(
        50, ge=2, le=1000, description="Clusters fitted on a diversity reference (lexical)"
    )

    @field_validator("dj_num_proc", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("settings_encryption_key")
    @classmethod
    def _key_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("SETTINGS_ENCRYPTION_KEY must be at least 32 characters")
        return value

    @field_validator("data_dir")
    @classmethod
    def _data_dir_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("DATA_DIR must be an absolute path")
        return value

    @property
    def database_url_sync(self) -> str:
        """The same database through psycopg2, for Celery workers and Alembic.

        Derived, not configured separately: two variables that must name one database can
        drift apart, and a worker writing to a different database than the API reads is a
        silent failure.
        """
        url = self.database_url
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg2", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url


class ConfigurationError(RuntimeError):
    """Configuration is missing or invalid. The message names every offending variable."""


def load_settings() -> Settings:
    """Build settings from the environment, or raise naming what is missing.

    pydantic's own error lists field names in lower case among other noise; this message names
    the environment variables an operator has to set.
    """
    try:
        return Settings()
    except ValidationError as exc:
        problems: list[str] = []
        for error in exc.errors():
            name = str(error["loc"][0]).upper() if error["loc"] else "?"
            if error["type"] == "missing":
                problems.append(f"{name} is required and not set")
            else:
                problems.append(f"{name}: {error['msg']}")
        raise ConfigurationError("Invalid configuration: " + "; ".join(problems)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, loaded once."""
    return load_settings()
