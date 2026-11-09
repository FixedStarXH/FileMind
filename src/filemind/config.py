"""Validated runtime configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    app_origin: str = "http://127.0.0.1:5173"
    database_url: str | None = None
    redis_url: str | None = None
    session_secret: SecretStr | None = None
    session_cookie_name: str = "filemind_session"
    session_ttl_days: int = 7
    storage_root: Path = Path("./data/files")
    model_cache_dir: Path = Path("./data/models")

    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-flash"
    ai_enabled: bool = True

    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_model_revision: str | None = None
    embedding_dim: int = 512
    ocr_backend: str = "rapidocr"

    max_upload_bytes: int = 52_428_800
    max_pdf_pages: int = 200
    user_quota_bytes: int = 2_147_483_648
    ocr_concurrency: int = 1
    agent_concurrency: int = 2
    export_concurrency: int = 1
    task_lease_seconds: int = 120
    enable_ingestion_worker: bool = False

    run_max_llm_calls: int = 8
    run_max_tool_calls: int = 12
    run_max_input_tokens: int = 32_000
    run_max_output_tokens: int = 8_000
    run_active_timeout_seconds: int = 300
    approval_ttl_hours: int = 24
    export_ttl_hours: int = 24
    trash_retention_days: int = 30
    event_retention_days: int = 7
    audit_retention_days: int = 90

    @field_validator("embedding_dim")
    @classmethod
    def validate_embedding_dim(cls, value: int) -> int:
        if value != 512:
            raise ValueError("bge-small-zh-v1.5 requires EMBEDDING_DIM=512")
        return value

    @field_validator("ocr_backend")
    @classmethod
    def validate_ocr_backend(cls, value: str) -> str:
        if value != "rapidocr":
            raise ValueError("OCR_BACKEND currently supports only rapidocr")
        return value

    @field_validator(
        "max_upload_bytes",
        "max_pdf_pages",
        "user_quota_bytes",
        "ocr_concurrency",
        "agent_concurrency",
        "export_concurrency",
        "task_lease_seconds",
        "run_max_llm_calls",
        "run_max_tool_calls",
        "run_max_input_tokens",
        "run_max_output_tokens",
        "run_active_timeout_seconds",
        "approval_ttl_hours",
        "export_ttl_hours",
        "trash_retention_days",
        "event_retention_days",
        "audit_retention_days",
    )
    @classmethod
    def validate_positive_limits(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("configured limits must be greater than zero")
        return value

    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key and self.deepseek_api_key.get_secret_value().strip())

    @property
    def has_session_secret(self) -> bool:
        return bool(self.session_secret and self.session_secret.get_secret_value().strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
