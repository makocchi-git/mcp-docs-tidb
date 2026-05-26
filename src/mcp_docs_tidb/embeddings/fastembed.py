"""
FastEmbed-backed implementation of `EmbeddingProvider`.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastembed import TextEmbedding
from pydantic import PrivateAttr

from mcp_docs_tidb.embeddings.base import EmbeddingProvider


class FastEmbedProvider(EmbeddingProvider):
    """FastEmbed implementation of the embedding provider."""

    _embedding_model: Optional[TextEmbedding] = PrivateAttr(default=None)

    def __init__(self, model_name: str, **data: Any):
        # Resolve dimensions without downloading the model so that the MCP
        # server can complete its startup handshake immediately.
        dim = FastEmbedProvider._resolve_dim(model_name)
        super().__init__(
            provider="fastembed",
            model_name=model_name,
            dimensions=dim,
            **data,
        )

    @staticmethod
    def _resolve_dim(model_name: str) -> int:
        try:
            for m in TextEmbedding.list_supported_models():
                if m["model"] == model_name:
                    return int(m["dim"])
        except Exception as exc:
            raise ValueError(
                f"Unknown FastEmbed model {model_name!r}; "
                "check the model name or upgrade fastembed."
            ) from exc
        # Unknown / custom model: must instantiate to obtain the dimension.
        try:
            te = TextEmbedding(model_name)
            desc = te._get_model_description(model_name)
            return int(desc.dim or 0)
        except Exception as exc:
            raise ValueError(
                f"Unknown FastEmbed model {model_name!r}; "
                "check the model name or upgrade fastembed."
            ) from exc

    def _model(self) -> TextEmbedding:
        """Return the TextEmbedding instance, downloading the model on first call."""
        if self._embedding_model is None:
            try:
                self._embedding_model = TextEmbedding(self.model_name)
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to load FastEmbed model {self.model_name!r}: {exc}"
                ) from exc
        return self._embedding_model

    def get_source_embedding(
        self,
        source: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        embeddings = list(self._model().passage_embed([source]))
        return embeddings[0].tolist()

    def get_source_embeddings(
        self,
        sources: list[Any],
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[list[float]]:
        embeddings = list(self._model().passage_embed(sources))
        return [e.tolist() for e in embeddings]

    def get_query_embedding(
        self,
        query: Any,
        source_type: Optional[Literal["text", "image"]] = "text",
        **kwargs: Any,
    ) -> list[float]:
        embeddings = list(self._model().query_embed([query]))
        return embeddings[0].tolist()
