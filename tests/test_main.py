"""Tests for `main.py` CLI entry point."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def _run_main(argv: list[str]) -> MagicMock:
    """Run main() with given argv, returning the mock mcp object."""
    mock_mcp = MagicMock()
    mock_server_mod = MagicMock()
    mock_server_mod.mcp = mock_mcp

    with patch.dict("sys.modules", {"mcp_docs_tidb.server": mock_server_mod}), \
         patch("sys.argv", argv):
        # Re-import to pick up the patched sys.modules at call time
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        main_mod.main()

    return mock_mcp


def test_default_transport_is_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_mcp = MagicMock()
    mock_server_mod = MagicMock()
    mock_server_mod.mcp = mock_mcp

    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb"])
    with patch.dict("sys.modules", {"mcp_docs_tidb.server": mock_server_mod}):
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        main_mod.main()

    mock_mcp.run.assert_called_once_with(transport="stdio")


def test_streamable_http_transport_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_mcp = MagicMock()
    mock_server_mod = MagicMock()
    mock_server_mod.mcp = mock_mcp

    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb", "--transport", "streamable-http"])
    with patch.dict("sys.modules", {"mcp_docs_tidb.server": mock_server_mod}):
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        main_mod.main()

    mock_mcp.run.assert_called_once_with(transport="streamable-http")


def test_sse_transport_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_mcp = MagicMock()
    mock_server_mod = MagicMock()
    mock_server_mod.mcp = mock_mcp

    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb", "--transport", "sse"])
    with patch.dict("sys.modules", {"mcp_docs_tidb.server": mock_server_mod}):
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        main_mod.main()

    mock_mcp.run.assert_called_once_with(transport="sse")


def test_invalid_transport_exits_with_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_docs_tidb.main import main

    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb", "--transport", "ftp"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


def test_server_import_error_exits_with_code_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb"])
    with patch.dict("sys.modules", {"mcp_docs_tidb.server": None}):  # type: ignore[dict-item]
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        with pytest.raises(SystemExit) as exc_info:
            main_mod.main()
    assert exc_info.value.code == 1


def test_connector_close_called_after_run(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_mcp = MagicMock()
    mock_connector = MagicMock()
    mock_mcp.tidb_connector = mock_connector
    mock_server_mod = MagicMock()
    mock_server_mod.mcp = mock_mcp

    monkeypatch.setattr(sys, "argv", ["mcp-docs-tidb"])
    with patch.dict("sys.modules", {"mcp_docs_tidb.server": mock_server_mod}):
        import importlib
        import mcp_docs_tidb.main as main_mod
        importlib.reload(main_mod)
        main_mod.main()

    mock_connector.close.assert_called_once()
