from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "get-myhome-ai"
    app_version: str = "0.1.0"
    schema_version: str = "v0.3"
    extractor_version: str = "0.1.0"
    prompt_version: str = "extract-v1"
    ai_api_key: Annotated[SecretStr, Field(min_length=32)] | None = None
    enable_docs: bool = False
    allow_unauthenticated_dev: bool = False
    allow_unrestricted_pdf_hosts_dev: bool = False

    ai_provider: Literal["ollama", "openai", "fixture"] = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: float = Field(default=180.0, gt=0)
    ollama_max_attempts: int = Field(default=3, ge=1, le=3)
    # The extraction schema, Korean source text, and bounded JSON answer can
    # exceed 8K tokens on dense payment tables. 12K still fully offloads the
    # default Qwen3 8B model on the project's 8GB GPU while avoiding Ollama's
    # silent context shifting observed at 8K.
    ollama_num_ctx: int = Field(default=12288, ge=2048)
    ollama_num_predict: int = Field(default=4096, ge=256)
    ollama_chunk_max_chars: int = Field(default=10_000, ge=2_000)
    ollama_keep_alive: str = "10m"
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
    max_concurrent_analyses: int = Field(default=1, ge=1)
    analysis_queue_timeout_seconds: float = Field(default=1.0, gt=0)
    analysis_timeout_seconds: float = Field(default=300.0, gt=0)
    readiness_timeout_seconds: float = Field(default=5.0, gt=0)

    fixture_dir: Path = Path("tests/fixtures/golden")
    auto_artifact_dir: Path = Path("artifacts/auto")
    reviewed_artifact_dir: Path = Path("artifacts/reviewed")

    @property
    def allowed_pdf_hosts(self) -> set[str]:
        return {host.strip().lower() for host in self.pdf_allowed_hosts.split(",") if host.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
