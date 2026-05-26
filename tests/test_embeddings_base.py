"""Tests for `embeddings/base.py`."""

from __future__ import annotations

from typing import Any, Optional, Literal

import pytest

from mcp_docs_tidb.embeddings.base import EmbeddingProvider


class _MinimalProvider(EmbeddingProvider):
    """Concrete subclass for testing the base class."""

    def get_query_embedding(
        self, query: Any, source_type: Optional[Literal["text", "image"]] = "text", **kwargs: Any
    ) -> list[float]:
        return [0.0]

    def get_source_embedding(
        self, source: Any, source_type: Optional[Literal["text", "image"]] = "text", **kwargs: Any
    ) -> list[float]:
        return [0.0]


def test_get_vector_size_raises_when_dimensions_is_none() -> None:
    provider = _MinimalProvider(provider="test", model_name="test", dimensions=None)
    with pytest.raises(ValueError, match="did not declare a dimension"):
        provider.get_vector_size()


def test_get_vector_size_returns_dimensions_when_set() -> None:
    provider = _MinimalProvider(provider="test", model_name="test", dimensions=64)
    assert provider.get_vector_size() == 64


def test_get_source_embeddings_default_calls_get_source_embedding() -> None:
    called_with: list[Any] = []

    class _TrackingProvider(_MinimalProvider):
        def get_source_embedding(
            self, source: Any, source_type: Optional[Literal["text", "image"]] = "text", **kwargs: Any
        ) -> list[float]:
            called_with.append(source)
            return [float(len(str(source)))]

    provider = _TrackingProvider(provider="test", model_name="test", dimensions=1)
    result = provider.get_source_embeddings(["a", "bb", "ccc"])
    assert called_with == ["a", "bb", "ccc"]
    assert result == [[1.0], [2.0], [3.0]]
