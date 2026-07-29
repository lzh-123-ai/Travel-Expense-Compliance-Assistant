from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise RAG Assistant"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False  # 调试模式，默认False
    api_v1_prefix: str = "/api/v1"  # API前缀,默认"/api/v1"
    database_url: str = "postgresql+asyncpg://rag_user:rag_password@localhost:5432/enterprise_rag"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    max_document_size_bytes: int = 10 * 1024 * 1024
    max_docx_uncompressed_size_bytes: int = 50 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
