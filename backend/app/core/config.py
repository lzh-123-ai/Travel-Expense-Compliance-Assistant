"""基于环境变量的配置，用于组装基础设施适配器。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "Enterprise RAG Assistant"
    app_env: Literal["development", "test", "production"] = "development"
    app_debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://rag_user:rag_password@localhost:5432/enterprise_rag"
    upload_dir: Path = PROJECT_ROOT / "uploads"
    max_document_size_bytes: int = 10 * 1024 * 1024
    max_docx_uncompressed_size_bytes: int = 50 * 1024 * 1024
    # 扫描版 PDF 的 OCR 是显式后处理；普通解析不读取这些配置或加载模型。
    ocr_language: str = "ch"
    ocr_min_confidence: float = 0.85
    ocr_pdf_render_scale: float = 2.0
    embedding_provider: Literal["sentence_transformers"] = "sentence_transformers"
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    embedding_model_path: Path | None = None
    embedding_dimension: int = 512
    embedding_batch_size: int = 32
    embedding_query_instruction: str = "为这个句子生成表示以用于检索相关文章："
    dense_search_max_top_k: int = 20
    keyword_tokenizer_version: str = "domain_bigram_v1"
    answer_provider: Literal["bailian"] = "bailian"
    answer_model_name: str = "qwen-plus"
    answer_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_api_key: SecretStr | None = None
    answer_temperature: float = 0.0
    answer_enable_thinking: bool = False
    answer_timeout_seconds: float = 60.0
    answer_max_retries: int = 2
    # 价格由部署者显式填写；默认 0 只记录 token，不冒充真实账单。
    answer_input_cost_per_million_usd: float = 0.0
    answer_output_cost_per_million_usd: float = 0.0
    answer_prompt_version: Literal["v0", "v1"] = "v1"
    # 工具路由默认保持规则基线；生产联调时显式切换为 bailian。
    tool_routing_provider: Literal["rule", "bailian"] = "rule"
    tool_routing_model_name: str = "qwen-plus"
    tool_routing_temperature: float = 0.0
    tool_routing_timeout_seconds: float = 30.0
    tool_routing_max_retries: int = 2
    # 生产环境必须通过环境变量覆盖 JWT 开发默认值。
    jwt_secret: SecretStr = SecretStr("development-only-change-this-jwt-secret")
    jwt_issuer: str = "enterprise-rag-assistant"
    jwt_expire_minutes: int = 60

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
