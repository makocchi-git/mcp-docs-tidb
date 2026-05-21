"""
Embedding provider abstraction for mcp-docs-tidb.

`EmbeddingProvider` is a thin subclass of pytidb's `BaseEmbeddingFunction`
so the same object can be plugged directly into pytidb's `VectorField`
(via `provider.VectorField(source_field="content")`) and reused at query
time when computing query vectors out-of-band.

The pytidb base class is synchronous; we expose synchronous helpers as
well to keep the rest of the codebase free of asyncio plumbing.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Literal, Optional

from pytidb.embeddings.base import BaseEmbeddingFunction


class EmbeddingProvider(BaseEmbeddingFunction):
    """Base class for embedding providers used by mcp-docs-tidb."""

    @abstractmethod
    def get_query_embedding(
        self,
        query: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        raise NotImplementedError

    @abstractmethod
    def get_source_embedding(
        self,
        source: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        raise NotImplementedError

    def get_source_embeddings(
        self,
        sources: list[Any],
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[list[float]]:
        return [
            self.get_source_embedding(s, source_type=source_type, **kwargs)
            for s in sources
        ]

    def get_vector_size(self) -> int:
        if self.dimensions is None:
            raise ValueError("Embedding provider did not declare a dimension.")
        return self.dimensions
