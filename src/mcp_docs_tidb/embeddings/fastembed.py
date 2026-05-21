"""
FastEmbed-backed implementation of `EmbeddingProvider`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastembed import TextEmbedding
from fastembed.common.model_description import DenseModelDescription
from pydantic import PrivateAttr

from mcp_docs_tidb.embeddings.base import EmbeddingProvider


class FastEmbedProvider(EmbeddingProvider):
    """FastEmbed implementation of the embedding provider."""

    _embedding_model: TextEmbedding = PrivateAttr()

    def __init__(self, model_name: str, **data: Any):
        embedding_model = TextEmbedding(model_name)
        description: DenseModelDescription = (
            embedding_model._get_model_description(model_name)
        )
        super().__init__(
            provider="fastembed",
            model_name=model_name,
            dimensions=description.dim,
            **data,
        )
        self._embedding_model = embedding_model

    def get_source_embedding(
        self,
        source: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        embeddings = list(self._embedding_model.passage_embed([source]))
        return embeddings[0].tolist()

    def get_source_embeddings(
        self,
        sources: list[Any],
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[list[float]]:
        embeddings = list(self._embedding_model.passage_embed(sources))
        return [e.tolist() for e in embeddings]

    def get_query_embedding(
        self,
        query: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        embeddings = list(self._embedding_model.query_embed([query]))
        return embeddings[0].tolist()
