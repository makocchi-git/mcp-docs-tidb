"""
Regression tests for `TiDBSettings` env-var handling.

Two scenarios are covered:

* The OS-level ``USER`` (and friends) must NOT leak into the ``user`` field
  through pydantic-settings' case-insensitive field-name fallback.
* Setting an alias to an empty string (e.g. ``TIDB_USER=``) must behave the
  same as leaving the variable unset — the documented default applies.
"""

from __future__ import annotations

import pytest

from mcp_docs_tidb.settings import TiDBSettings


@pytest.fixture(autouse=True)
def _clear_tidb_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "TIDB_HOST",
        "TIDB_PORT",
        "TIDB_USER",
        "TIDB_PASSWORD",
        "TIDB_DATABASE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_os_user_env_does_not_leak_into_user_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("USER", "some-os-user")
    assert TiDBSettings().user == "root"


def test_empty_tidb_user_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIDB_USER", "")
    assert TiDBSettings().user == "root"


def test_empty_tidb_database_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIDB_DATABASE", "")
    assert TiDBSettings().database == "test"


def test_explicit_tidb_user_overrides_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TIDB_USER", "alice")
    assert TiDBSettings().user == "alice"


def test_uppercase_host_env_does_not_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST", "leaked.example.com")
    assert TiDBSettings().host == "127.0.0.1"
