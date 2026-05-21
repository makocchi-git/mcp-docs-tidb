from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.embeddings.types import EmbeddingProviderType
from mcp_docs_tidb.settings import EmbeddingProviderSettings


def create_embedding_provider(settings: EmbeddingProviderSettings) -> EmbeddingProvider:
    """
    Create an embedding provider based on the specified type.
    """
    if settings.provider_type == EmbeddingProviderType.FASTEMBED:
        from mcp_docs_tidb.embeddings.fastembed import FastEmbedProvider

        return FastEmbedProvider(settings.model_name)
    raise ValueError(f"Unsupported embedding provider: {settings.provider_type}")
