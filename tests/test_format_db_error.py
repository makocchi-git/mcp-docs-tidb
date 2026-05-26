"""
Unit tests for ``format_db_error``. The helper is the single source of
truth for the operator-friendly error string emitted by both the CLI and
the MCP tools when TiDB rejects a request — keeping its output stable is
worth a few targeted assertions.
"""

from __future__ import annotations

from sqlalchemy.exc import OperationalError

from mcp_docs_tidb.settings import TiDBSettings
from mcp_docs_tidb.tidb import format_db_error


def _settings() -> TiDBSettings:
    return TiDBSettings(
        host="db.example.com",
        port=4000,
        user="alice",
        password="secret",
        database="kb",
    )


def test_format_db_error_includes_connection_target() -> None:
    msg = format_db_error(RuntimeError("boom"), _settings())
    assert "db.example.com:4000" in msg
    assert "user='alice'" in msg
    assert "database='kb'" in msg


def test_format_db_error_surfaces_underlying_dbapi_error() -> None:
    # SQLAlchemy's wrapped exceptions expose the DBAPI cause via `.orig`.
    underlying = RuntimeError("Access denied for user 'alice'@'host'")
    wrapped = OperationalError("statement", {}, underlying)
    msg = format_db_error(wrapped, _settings())
    assert "Access denied for user 'alice'@'host'" in msg


def test_format_db_error_falls_back_to_exc_str_when_no_orig() -> None:
    msg = format_db_error(RuntimeError("plain message"), _settings())
    assert "plain message" in msg


def test_format_db_error_mentions_hint_env_vars() -> None:
    msg = format_db_error(RuntimeError("x"), _settings())
    assert "Hint:" in msg
    for var in ("TIDB_HOST", "TIDB_PORT", "TIDB_USER", "TIDB_PASSWORD", "TIDB_DATABASE"):
        assert var in msg


def test_format_db_error_does_not_leak_password() -> None:
    msg = format_db_error(RuntimeError("auth failure"), _settings())
    assert "secret" not in msg


def test_format_db_error_orig_falsy_falls_back_to_exc() -> None:
    # SQLAlchemy sometimes wraps with .orig=None; must not emit "None" as the message.
    from sqlalchemy.exc import OperationalError

    wrapped = OperationalError("stmt", {}, None)
    msg = format_db_error(wrapped, _settings())
    # Should not contain raw "None" from orig; should still have the outer exc str.
    assert "Hint:" in msg


def test_format_db_error_handles_filenotfounderror() -> None:
    msg = format_db_error(FileNotFoundError("no such file"), _settings())
    assert "db.example.com" in msg
    assert "Hint:" in msg
