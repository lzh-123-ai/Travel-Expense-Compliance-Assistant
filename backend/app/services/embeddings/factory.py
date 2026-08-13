"""根据应用配置选择向量化 Provider 的组装入口。"""

from functools import lru_cache

from app.core.config import PROJECT_ROOT, get_settings
from app.services.embeddings.contracts import EmbeddingProvider
from app.services.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    model_path = settings.embedding_model_path
    if model_path is not None and not model_path.is_absolute():
        model_path = PROJECT_ROOT / model_path
    return SentenceTransformerEmbeddingProvider(
        model_name=settings.embedding_model_name,
        model_path=model_path,
        dimension=settings.embedding_dimension,
        batch_size=settings.embedding_batch_size,
        query_instruction=settings.embedding_query_instruction,
    )
