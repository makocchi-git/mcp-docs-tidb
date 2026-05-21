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

from tests.conftest import DeterministicEmbeddingProvider


class _StubContext:
    async def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _StubConnector:
    def __init__(self) -> None:
        self.stored: list[tuple[Entry, str | None]] = []

    def store(self, entry: Entry, *, collection_name: str | None = None) -> None:
        self.stored.append((entry, collection_name))


async def _registered_tool_names(server: TiDBMCPServer) -> set[str]:
    tools = await server.get_tools()
    return set(tools.keys())


async def _tool_param_names(server: TiDBMCPServer, name: str) -> set[str]:
    tools = await server.get_tools()
    schema = tools[name].parameters
    return set(schema.get("properties", {}).keys())


@pytest.mark.asyncio
async def test_default_registers_all_three_tools() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    assert await _registered_tool_names(server) == {
        "docs-tidb-find",
        "docs-tidb-store",
        "docs-tidb-ingest",
    }


@pytest.mark.asyncio
async def test_read_only_hides_store_and_ingest_tools() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(read_only=True),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    assert await _registered_tool_names(server) == {"docs-tidb-find"}


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
    assert "collection_name" not in find_params
    assert "collection_name" not in store_params
    assert "collection_name" not in ingest_params


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

    tools = await server.get_tools()
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
async def test_store_tool_without_mtime_still_stamps_ingested_at() -> None:
    server = TiDBMCPServer(
        tool_settings=ToolSettings(),
        tidb_settings=TiDBSettings(),
        embedding_provider=DeterministicEmbeddingProvider(dim=8),
    )
    stub = _StubConnector()
    server.tidb_connector = stub  # type: ignore[assignment]

    tools = await server.get_tools()
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
