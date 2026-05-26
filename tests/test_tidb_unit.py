"""Unit tests for TiDBConnector that do not require a live TiDB instance."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_docs_tidb.settings import TiDBSettings
from mcp_docs_tidb.tidb import TiDBConnector

from tests.conftest import DeterministicEmbeddingProvider


def _make_connector(**kwargs: Any) -> TiDBConnector:
    return TiDBConnector(
        settings=TiDBSettings(**kwargs),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )


class TestResolveCollection:
    def test_uses_provided_name(self) -> None:
        conn = _make_connector()
        assert conn._resolve_collection("my_col") == "my_col"

    def test_uses_default_when_none_provided(self) -> None:
        conn = _make_connector(collection_name="default_col")
        assert conn._resolve_collection(None) == "default_col"

    def test_raises_when_no_name_and_no_default(self) -> None:
        conn = _make_connector()
        with pytest.raises(ValueError, match="No collection name provided"):
            conn._resolve_collection(None)

    def test_empty_string_falls_back_to_default(self) -> None:
        conn = _make_connector(collection_name="fallback_col")
        assert conn._resolve_collection("") == "fallback_col"


class TestClose:
    def test_close_before_connect_is_safe(self) -> None:
        conn = _make_connector()
        conn.close()  # Must not raise

    def test_close_twice_is_idempotent(self) -> None:
        conn = _make_connector()
        mock_client = MagicMock()
        conn._client = mock_client
        conn._tables["t"] = MagicMock()

        conn.close()
        conn.close()

        mock_client.disconnect.assert_called_once()
        assert conn._client is None
        assert conn._tables == {}


class TestCloseIdempotent:
    def test_close_with_disconnect_error_still_clears_state(self) -> None:
        conn = _make_connector()
        mock_client = MagicMock()
        mock_client.disconnect.side_effect = RuntimeError("network gone")
        conn._client = mock_client
        conn._tables["t"] = MagicMock()

        conn.close()  # Must not raise even when disconnect() raises

        assert conn._client is None
        assert conn._tables == {}

    def test_tables_cleared_after_close(self) -> None:
        conn = _make_connector()
        mock_client = MagicMock()
        conn._client = mock_client
        conn._tables["a"] = MagicMock()
        conn._tables["b"] = MagicMock()

        conn.close()
        assert conn._tables == {}


class TestGetTable:
    def test_concurrent_calls_return_same_table(self) -> None:
        import threading

        conn = _make_connector()
        mock_client = MagicMock()
        conn._client = mock_client
        mock_table = MagicMock()
        mock_client.create_table.return_value = mock_table

        results: list[Any] = []

        with patch("mcp_docs_tidb.tidb._build_chunk_model", return_value=MagicMock()):
            def worker() -> None:
                results.append(conn._get_table("col"))

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # All threads must see the same table object stored in cache
        assert all(r is results[0] for r in results)
        assert "col" in conn._tables

    def test_warns_when_table_count_exceeds_256(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        conn = _make_connector()
        mock_client = MagicMock()
        conn._client = mock_client

        # Pre-fill 256 dummy entries
        for i in range(256):
            conn._tables[f"dummy_{i}"] = MagicMock()

        mock_client.create_table.return_value = MagicMock()
        with patch("mcp_docs_tidb.tidb._build_chunk_model", return_value=MagicMock()):
            with caplog.at_level(logging.WARNING, logger="mcp_docs_tidb.tidb"):
                conn._get_table("new_table")

        assert any("256" in r.message for r in caplog.records)


class TestGetClientSslKwargs:
    def test_ssl_kwargs_passed_when_configured(self) -> None:
        conn = TiDBConnector(
            settings=TiDBSettings(ssl_verify_cert=True, ssl_ca="/etc/ssl/cert.pem"),
            embedding_provider=DeterministicEmbeddingProvider(dim=8),
        )
        mock_client = MagicMock()
        with patch("mcp_docs_tidb.tidb.TiDBClient") as mock_tidb_cls:
            mock_tidb_cls.connect.return_value = mock_client
            conn._get_client()

        _, kwargs = mock_tidb_cls.connect.call_args
        assert kwargs.get("enable_ssl") is True
        assert kwargs.get("ssl_ca") == "/etc/ssl/cert.pem"

    def test_no_ssl_kwargs_when_not_configured(self) -> None:
        conn = TiDBConnector(
            settings=TiDBSettings(ssl_verify_cert=False),
            embedding_provider=DeterministicEmbeddingProvider(dim=8),
        )
        mock_client = MagicMock()
        with patch("mcp_docs_tidb.tidb.TiDBClient") as mock_tidb_cls:
            mock_tidb_cls.connect.return_value = mock_client
            conn._get_client()

        _, kwargs = mock_tidb_cls.connect.call_args
        assert "enable_ssl" not in kwargs
        assert "ssl_ca" not in kwargs


class TestGetClientTimeouts:
    def test_connect_timeout_passed_via_connect_args(self) -> None:
        conn = TiDBConnector(
            settings=TiDBSettings(connect_timeout=5.0, read_timeout=15.0),
            embedding_provider=DeterministicEmbeddingProvider(dim=8),
        )
        mock_client = MagicMock()
        with patch("mcp_docs_tidb.tidb.TiDBClient") as mock_tidb_cls:
            mock_tidb_cls.connect.return_value = mock_client
            conn._get_client()

        _, kwargs = mock_tidb_cls.connect.call_args
        connect_args = kwargs.get("connect_args", {})
        assert connect_args.get("connect_timeout") == 5
        assert connect_args.get("read_timeout") == 15

    def test_default_timeouts_are_present(self) -> None:
        conn = _make_connector()
        mock_client = MagicMock()
        with patch("mcp_docs_tidb.tidb.TiDBClient") as mock_tidb_cls:
            mock_tidb_cls.connect.return_value = mock_client
            conn._get_client()

        _, kwargs = mock_tidb_cls.connect.call_args
        connect_args = kwargs.get("connect_args", {})
        assert "connect_timeout" in connect_args
        assert "read_timeout" in connect_args
