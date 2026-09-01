from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "get-myhome-ai"
    app_version: str = "0.1.0"
    schema_version: str = "v0.3"
    extractor_version: str = "0.1.0"
    prompt_version: str = "extract-v1"

    ai_provider: Literal["openai", "fixture"] = "openai"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5-mini-2025-08-07"
    openai_timeout_seconds: float = Field(default=90.0, gt=0)

    pdf_download_timeout_seconds: float = Field(default=30.0, gt=0)
    pdf_max_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    pdf_allowed_hosts: str = ""
    pdf_text_timeout_seconds: float = Field(default=30.0, gt=0)
    max_pdf_pages: int = Field(default=200, ge=1)

    max_candidate_pages: int = Field(default=24, ge=1)
    max_candidate_chars: int = Field(default=180_000, ge=1)
    max_concurrent_analyses: int = Field(default=2, ge=1)

    fixture_dir: Path = Path("tests/fixtures/golden")
    auto_artifact_dir: Path = Path("artifacts/auto")
    reviewed_artifact_dir: Path = Path("artifacts/reviewed")

    @property
    def allowed_pdf_hosts(self) -> set[str]:
        return {host.strip().lower() for host in self.pdf_allowed_hosts.split(",") if host.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
