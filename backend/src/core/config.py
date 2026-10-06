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
    millm_base_url: str | None = Field(None, description="miLLM base URL, for health only")
    mistudio_base_url: str | None = Field(None, description="miStudio base URL, for health only")
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
    janitor_interval_seconds: float = Field(
        120.0, gt=0, description="Beat interval for the job janitor"
    )
    celery_task_always_eager: bool = Field(
        False, description="Run tasks in-process (tests only); never set in a deployment"
    )

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
