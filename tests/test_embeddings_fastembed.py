"""Tests for `embeddings/fastembed.py` — model download is always mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch



def test_resolve_dim_uses_static_list_for_known_model() -> None:
    from mcp_docs_tidb.embeddings.fastembed import FastEmbedProvider

    with patch("mcp_docs_tidb.embeddings.fastembed.TextEmbedding") as mock_te:
        mock_te.list_supported_models.return_value = [
            {"model": "known/model", "dim": 256},
        ]
        result = FastEmbedProvider._resolve_dim("known/model")
    assert result == 256
    mock_te.assert_not_called()


def test_resolve_dim_falls_back_to_instantiation_for_unknown_model() -> None:
    from mcp_docs_tidb.embeddings.fastembed import FastEmbedProvider

    mock_desc = MagicMock()
    mock_desc.dim = 512
    mock_te_instance = MagicMock()
    mock_te_instance._get_model_description.return_value = mock_desc

    with patch("mcp_docs_tidb.embeddings.fastembed.TextEmbedding") as mock_te:
        mock_te.list_supported_models.return_value = []
        mock_te.return_value = mock_te_instance
        result = FastEmbedProvider._resolve_dim("custom/model")

    assert result == 512
    mock_te_instance._get_model_description.assert_called_once_with("custom/model")


def test_model_is_lazy_initialised() -> None:
    from mcp_docs_tidb.embeddings.fastembed import FastEmbedProvider

    mock_te_instance = MagicMock()
    instantiation_count = {"n": 0}

    def mock_constructor(model_name: str) -> MagicMock:
        instantiation_count["n"] += 1
        return mock_te_instance

    with patch("mcp_docs_tidb.embeddings.fastembed.TextEmbedding") as mock_te:
        mock_te.list_supported_models.return_value = [{"model": "x/m", "dim": 8}]
        mock_te.side_effect = mock_constructor

        provider = FastEmbedProvider("x/m")
        # Constructor should not have been called yet (dim was resolved from list)
        assert instantiation_count["n"] == 0

        # First _model() call triggers the download
        _ = provider._model()
        assert instantiation_count["n"] == 1

        # Second call reuses the cached instance
        _ = provider._model()
        assert instantiation_count["n"] == 1
