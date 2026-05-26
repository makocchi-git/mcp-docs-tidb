"""Async tool invocation tests for TiDBMCPServer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mcp_docs_tidb.mcp_server import TiDBMCPServer
from mcp_docs_tidb.settings import FilterableField, TiDBSettings, ToolSettings
from mcp_docs_tidb.tidb import Entry

from tests.conftest import DeterministicEmbeddingProvider, _tools_by_name


class _StubContext:
    async def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _SpyConnector:
    """Connector that records search calls and returns an empty list."""

    def __init__(self) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.store_calls: list[tuple[Entry, str | None]] = []

    def store(self, entry: Entry, *, collection_name: str | None = None) -> None:
        self.store_calls.append((entry, collection_name))

    def search(
        self,
        query: str,
        *,
        collection_name: str | None = None,
        limit: int = 10,
        dict_filter: dict[str, Any] | None = None,
    ) -> list[Entry]:
        self.search_calls.append(
            {"query": query, "collection_name": collection_name, "dict_filter": dict_filter}
        )
        return []

    def list_sources(self, *, collection_name: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        return []


class _ValueErrorConnector:
    """Connector whose store/search raise ValueError (e.g. invalid identifier)."""

    def store(self, *_args: Any, **_kwargs: Any) -> None:
        raise ValueError("Invalid TiDB table identifier: 'bad name'")

    def search(self, *_args: Any, **_kwargs: Any) -> list[Entry]:
        raise ValueError("Invalid metadata field name: '123'")

    def list_sources(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise ValueError("No collection name provided")


def _make_server(**tidb_kwargs: Any) -> TiDBMCPServer:
    return TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(**tidb_kwargs),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )


@pytest.mark.asyncio
async def test_find_tool_query_filter_translated_to_dict_filter() -> None:
    server = _make_server(allow_arbitrary_filter=True)
    spy = _SpyConnector()
    server.tidb_connector = spy  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    find_fn = tools["docs-tidb-find"].fn

    await find_fn(
        ctx=_StubContext(),
        query="test query",
        collection_name="kb",
        query_filter={"must": [{"field": "category", "op": "==", "value": "work"}]},
    )

    assert len(spy.search_calls) == 1
    assert spy.search_calls[0]["dict_filter"] == {
        "metadata.category": {"$eq": "work"}
    }


@pytest.mark.asyncio
async def test_find_tool_with_filterable_fields_via_wrap_filters() -> None:
    server = _make_server(
        filterable_fields=[
            FilterableField(name="category", description="cat", field_type="keyword", condition="==")
        ]
    )
    spy = _SpyConnector()
    server.tidb_connector = spy  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    find_fn = tools["docs-tidb-find"].fn

    await find_fn(
        ctx=_StubContext(),
        query="test",
        collection_name="kb",
        category="docs",
    )

    assert len(spy.search_calls) == 1
    assert spy.search_calls[0]["dict_filter"] == {
        "metadata.category": {"$eq": "docs"}
    }


@pytest.mark.asyncio
async def test_find_tool_value_error_returns_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _make_server()
    server.tidb_connector = _ValueErrorConnector()  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    find_fn = tools["docs-tidb-find"].fn

    result = await find_fn(ctx=_StubContext(), query="q", collection_name="bad name")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].startswith("Error:")


@pytest.mark.asyncio
async def test_store_tool_value_error_returns_friendly_message() -> None:
    server = _make_server()
    server.tidb_connector = _ValueErrorConnector()  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    store_fn = tools["docs-tidb-store"].fn

    result = await store_fn(
        ctx=_StubContext(),
        information="hello",
        collection_name="bad name",
    )
    assert isinstance(result, str)
    assert result.startswith("Error:")


@pytest.mark.asyncio
async def test_ingest_tool_path_not_found_returns_friendly_message(
    tmp_path: Path,
) -> None:
    server = _make_server()
    spy = _SpyConnector()
    server.tidb_connector = spy  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    ingest_fn = tools["docs-tidb-ingest"].fn

    nonexistent = str(tmp_path / "does_not_exist.md")
    result = await ingest_fn(
        ctx=_StubContext(),
        paths=[nonexistent],
        collection_name="kb",
    )
    assert result.startswith("Error:")
    assert nonexistent in result


@pytest.mark.asyncio
async def test_ingest_tool_no_files_matched_returns_message(
    tmp_path: Path,
) -> None:
    server = _make_server()
    spy = _SpyConnector()
    server.tidb_connector = spy  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    ingest_fn = tools["docs-tidb-ingest"].fn

    result = await ingest_fn(
        ctx=_StubContext(),
        paths=[str(tmp_path)],  # empty directory, default glob *.md matches nothing
        collection_name="kb",
    )
    assert isinstance(result, str)
    assert "0" in result or "No" in result or "chunk" in result
