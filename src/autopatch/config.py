"""Application settings loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for AutoPatch.

    Required secrets are optional at import time so unit tests can construct
    partial settings. CLI entrypoints call ``require_for_run`` before a live run.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    github_token: str | None = None

    llm_provider: Literal["claude", "openai"] = "claude"
    llm_model: str = "claude-sonnet-4-6"

    max_files_per_patch: int = Field(default=5, ge=1, le=50)
    max_retries: int = Field(default=3, ge=0, le=10)
    sandbox_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    run_timeout_seconds: int = Field(default=1800, ge=60, le=7200)

    work_dir: Path = Field(default=Path(".autopatch/work"))
    log_dir: Path = Field(default=Path(".autopatch/logs"))

    sandbox_image: str = "python:3.11-slim"
    docker_network_disabled: bool = True

    @field_validator("work_dir", "log_dir", mode="before")
    @classmethod
    def _coerce_path(cls, value: object) -> Path:
        return Path(str(value))

    def require_for_run(self, *, need_github: bool = True, need_llm: bool = True) -> None:
        """Fail closed when secrets required for a live run are missing."""
        missing: list[str] = []
        if need_llm:
            if self.llm_provider == "claude" and not self.anthropic_api_key:
                missing.append("ANTHROPIC_API_KEY")
            if self.llm_provider == "openai" and not self.openai_api_key:
                missing.append("OPENAI_API_KEY")
        if need_github and not self.github_token:
            missing.append("GITHUB_TOKEN")
        if missing:
            raise ValueError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )

    def ensure_dirs(self) -> None:
        """Create work and log directories if they do not exist."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()
