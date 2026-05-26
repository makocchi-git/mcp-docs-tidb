"""Tests for `ingest.py` CLI (_run_cli / main) — no TiDB required."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from mcp_docs_tidb.ingest import _build_argparser, _run_cli


def _make_args(
    tmp_path: Path,
    *,
    paths: list[str] | None = None,
    collection: str = "test_col",
    chunk_chars: int = 2000,
    overlap: int = 200,
    recursive: bool = False,
    glob: str = "*.md",
    replace: bool = True,
    only_modified: bool = False,
    truncate_collection: bool = False,
    exclude_globs: list[str] | None = None,
    extra_metadata: list[str] | None = None,
    verbose: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        paths=paths or [str(tmp_path)],
        collection=collection,
        chunk_chars=chunk_chars,
        overlap=overlap,
        recursive=recursive,
        glob=glob,
        replace=replace,
        only_modified=only_modified,
        truncate_collection=truncate_collection,
        exclude_globs=exclude_globs or [],
        extra_metadata=extra_metadata or [],
        verbose=verbose,
    )


def test_no_matching_files_returns_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _make_args(tmp_path)
    code = _run_cli(args)
    assert code == 1
    captured = capsys.readouterr()
    assert "No files matched" in captured.err


def test_invalid_extra_metadata_returns_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "x.md").write_text("hello")
    args = _make_args(tmp_path, extra_metadata=["bad-format"])
    code = _run_cli(args)
    assert code == 1
    captured = capsys.readouterr()
    assert "KEY=VALUE" in captured.err or "Error" in captured.err


def test_sqlalchemy_error_returns_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.md").write_text("hello")
    args = _make_args(tmp_path)

    def _exploding_ingest_paths(*_args: Any, **_kwargs: Any) -> int:
        raise SQLAlchemyError("db failure")

    monkeypatch.setattr("mcp_docs_tidb.ingest.ingest_paths", _exploding_ingest_paths)
    mock_provider = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.create_embedding_provider", lambda _: mock_provider)
    mock_connector = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.TiDBConnector", lambda **_kw: mock_connector)

    code = _run_cli(args)
    assert code == 2
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_verbose_flag_reraises_after_friendly_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.md").write_text("hello")
    args = _make_args(tmp_path, verbose=True)

    def _exploding_ingest_paths(*_args: Any, **_kwargs: Any) -> int:
        raise SQLAlchemyError("db failure verbose")

    monkeypatch.setattr("mcp_docs_tidb.ingest.ingest_paths", _exploding_ingest_paths)
    mock_provider = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.create_embedding_provider", lambda _: mock_provider)
    mock_connector = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.TiDBConnector", lambda **_kw: mock_connector)

    with pytest.raises(SQLAlchemyError):
        _run_cli(args)

    captured = capsys.readouterr()
    assert "Error:" in captured.err


def test_connector_close_called_even_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.md").write_text("hello")
    args = _make_args(tmp_path)

    def _exploding_ingest_paths(*_args: Any, **_kwargs: Any) -> int:
        raise SQLAlchemyError("boom")

    monkeypatch.setattr("mcp_docs_tidb.ingest.ingest_paths", _exploding_ingest_paths)
    mock_provider = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.create_embedding_provider", lambda _: mock_provider)
    mock_connector = MagicMock()
    monkeypatch.setattr("mcp_docs_tidb.ingest.TiDBConnector", lambda **_kw: mock_connector)

    _run_cli(args)
    mock_connector.close.assert_called_once()


def test_argparser_has_required_collection_argument() -> None:
    parser = _build_argparser()
    with pytest.raises(SystemExit):
        parser.parse_args(["some/path"])


