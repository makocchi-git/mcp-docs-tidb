"""Tests for `_validate_identifier` in tidb.py."""

from __future__ import annotations

import pytest

from mcp_docs_tidb.tidb import _validate_identifier


def test_valid_names_are_returned() -> None:
    assert _validate_identifier("hello") == "hello"
    assert _validate_identifier("_private") == "_private"
    assert _validate_identifier("A1b2_c3") == "A1b2_c3"


def test_default_context_appears_in_message() -> None:
    with pytest.raises(ValueError, match="TiDB table identifier"):
        _validate_identifier("123bad")


def test_custom_context_appears_in_message() -> None:
    with pytest.raises(ValueError, match="metadata field name"):
        _validate_identifier("bad-name", context="metadata field name")


def test_digit_prefix_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("1leading_digit")


def test_hyphen_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("my-field")


def test_dot_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("a.b")


def test_sql_injection_attempt_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("x; DROP TABLE users--")


def test_space_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("my field")


def test_empty_string_is_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_identifier("")
