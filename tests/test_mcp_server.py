"""
Tests for `TiDBMCPServer` tool registration. These tests don't talk to TiDB —
they just verify that `setup_tools` produces the expected tool surface for
each configuration (default collection, read-only, filterable fields,
arbitrary filter).
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_docs_tidb.mcp_server import TiDBMCPServer
from mcp_docs_tidb.settings import FilterableField, TiDBSettings, ToolSettings
from mcp_docs_tidb.tidb import Entry

from tests.conftest import DeterministicEmbeddingProvider, _tools_by_name


class _StubContext:
    async def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _StubConnector:
    def __init__(self) -> None:
        self.stored: list[tuple[Entry, str | None]] = []

    def store(self, entry: Entry, *, collection_name: str | None = None) -> None:
        self.stored.append((entry, collection_name))


class _ExplodingConnector:
    """Connector stub whose store/search/* all raise SQLAlchemyError."""

    def __init__(self) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        self._exc_cls = SQLAlchemyError

    def store(self, *_args: Any, **_kwargs: Any) -> None:
        raise self._exc_cls("boom")

    def search(self, *_args: Any, **_kwargs: Any) -> list[Entry]:
        raise self._exc_cls("boom")

    def list_sources(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        raise self._exc_cls("boom")


class _ListStubConnector:
    """Connector stub that returns canned list_sources output."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[str | None] = []

    def list_sources(
        self, *, collection_name: str | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        self.calls.append(collection_name)
        return list(self._rows)


async def _registered_tool_names(server: TiDBMCPServer) -> set[str]:
    tools = await _tools_by_name(server)
    return set(tools.keys())


async def _tool_param_names(server: TiDBMCPServer, name: str) -> set[str]:
    tools = await _tools_by_name(server)
    schema = tools[name].parameters
    return set(schema.get("properties", {}).keys())


@pytest.mark.asyncio
async def test_default_registers_all_four_tools() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    assert await _registered_tool_names(server) == {
        "docs-tidb-find",
        "docs-tidb-store",
        "docs-tidb-ingest",
        "docs-tidb-list",
    }


@pytest.mark.asyncio
async def test_read_only_hides_store_and_ingest_tools() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(read_only=True),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    assert await _registered_tool_names(server) == {
        "docs-tidb-find",
        "docs-tidb-list",
    }


@pytest.mark.asyncio
async def test_default_collection_hides_collection_argument() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(collection_name="mcp_default"),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    find_params = await _tool_param_names(server, "docs-tidb-find")
    store_params = await _tool_param_names(server, "docs-tidb-store")
    ingest_params = await _tool_param_names(server, "docs-tidb-ingest")
    list_params = await _tool_param_names(server, "docs-tidb-list")
    assert "collection_name" not in find_params
    assert "collection_name" not in store_params
    assert "collection_name" not in ingest_params
    assert "collection_name" not in list_params


@pytest.mark.asyncio
async def test_ingest_tool_exposes_expected_args() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    params = await _tool_param_names(server, "docs-tidb-ingest")
    assert {
        "paths",
        "collection_name",
        "recursive",
        "glob",
        "chunk_chars",
        "overlap",
        "replace",
        "only_modified",
        "truncate_collection",
    }.issubset(params)


@pytest.mark.asyncio
async def test_filterable_fields_expose_typed_args() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(
            filterable_fields=[
                FilterableField(
                    name="category",
                    description="cat",
                    field_type="keyword",
                    condition="==",
                ),
                FilterableField(
                    name="year",
                    description="year",
                    field_type="integer",
                    condition=">=",
                ),
            ],
        ),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    params = await _tool_param_names(server, "docs-tidb-find")
    assert {"category", "year"}.issubset(params)
    assert "query_filter" not in params
    assert "dict_filter" not in params


@pytest.mark.asyncio
async def test_arbitrary_filter_exposes_query_filter() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(allow_arbitrary_filter=True),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    params = await _tool_param_names(server, "docs-tidb-find")
    assert "query_filter" in params
    assert "dict_filter" not in params


@pytest.mark.asyncio
async def test_no_filter_hides_all_filter_args() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    params = await _tool_param_names(server, "docs-tidb-find")
    assert "query_filter" not in params
    assert "dict_filter" not in params


@pytest.mark.asyncio
async def test_store_tool_exposes_mtime_argument() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    params = await _tool_param_names(server, "docs-tidb-store")
    assert "mtime" in params


@pytest.mark.asyncio
async def test_store_tool_records_mtime_and_ingested_at() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    stub = _StubConnector()
    server.tidb_connector = stub  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    store_fn = tools["docs-tidb-store"].fn

    await store_fn(
        ctx=_StubContext(),
        information="hello world",
        collection_name="kb",
        metadata={"category": "fruit"},
        mtime=1700000000.5,
    )

    assert len(stub.stored) == 1
    entry, collection = stub.stored[0]
    assert collection == "kb"
    assert entry.content == "hello world"
    assert entry.metadata is not None
    assert entry.metadata["category"] == "fruit"
    assert entry.metadata["mtime"] == 1700000000.5
    assert isinstance(entry.metadata["ingested_at"], float)


@pytest.mark.asyncio
async def test_store_tool_returns_friendly_error_on_db_failure() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    server.tidb_connector = _ExplodingConnector()  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    store_fn = tools["docs-tidb-store"].fn

    result = await store_fn(
        ctx=_StubContext(),
        information="hello",
        collection_name="kb",
    )

    assert "Error: failed to access TiDB" in result
    assert "Hint:" in result


@pytest.mark.asyncio
async def test_find_tool_returns_friendly_error_on_db_failure() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    server.tidb_connector = _ExplodingConnector()  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    find_fn = tools["docs-tidb-find"].fn

    result = await find_fn(
        ctx=_StubContext(),
        query="anything",
        collection_name="kb",
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert "Error: failed to access TiDB" in result[0]


@pytest.mark.asyncio
async def test_list_tool_returns_connector_rows() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    rows = [
        {
            "source": "/abs/a.md",
            "chunks": 3,
            "mtime": 1700000000.0,
            "ingested_at": 1700000100.0,
        },
        {
            "source": "/abs/b.md",
            "chunks": 1,
            "mtime": None,
            "ingested_at": 1700000200.0,
        },
    ]
    stub = _ListStubConnector(rows)
    server.tidb_connector = stub  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    list_fn = tools["docs-tidb-list"].fn

    result = await list_fn(ctx=_StubContext(), collection_name="kb")

    assert result == rows
    assert stub.calls == ["kb"]


@pytest.mark.asyncio
async def test_list_tool_returns_friendly_error_on_db_failure() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    server.tidb_connector = _ExplodingConnector()  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    list_fn = tools["docs-tidb-list"].fn

    result = await list_fn(ctx=_StubContext(), collection_name="kb")

    assert isinstance(result, str)
    assert "Error: failed to access TiDB" in result


@pytest.mark.asyncio
async def test_store_tool_without_mtime_still_stamps_ingested_at() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    stub = _StubConnector()
    server.tidb_connector = stub  # type: ignore[assignment]

    tools = await _tools_by_name(server)
    store_fn = tools["docs-tidb-store"].fn

    await store_fn(
        ctx=_StubContext(),
        information="hello",
        collection_name="kb",
    )

    entry, _ = stub.stored[0]
    assert entry.metadata is not None
    assert "mtime" not in entry.metadata
    assert isinstance(entry.metadata["ingested_at"], float)
