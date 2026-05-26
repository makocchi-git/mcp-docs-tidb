"""Tests for `embeddings/factory.py`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.settings import EmbeddingProviderSettings


def test_create_embedding_provider_returns_fastembed_for_default_settings() -> None:
    mock_instance = MagicMock()
    # FastEmbedProvider is imported inside the function body in factory.py,
    # so patch it at its definition site.
    with patch("mcp_docs_tidb.embeddings.fastembed.FastEmbedProvider") as mock_cls:
        mock_cls.return_value = mock_instance
        settings = EmbeddingProviderSettings()
        result = create_embedding_provider(settings)
    mock_cls.assert_called_once_with(settings.model_name)
    assert result is mock_instance


def test_create_embedding_provider_unsupported_type_raises() -> None:
    settings = EmbeddingProviderSettings()
    object.__setattr__(settings, "provider_type", "unsupported_type")
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        create_embedding_provider(settings)
