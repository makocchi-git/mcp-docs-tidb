"""
Integration tests against a live TiDB instance. Skipped automatically if
TiDB is unreachable. The test playground assumed by the README is::

    tiup playground v8.4.0 --tiflash 0 --db 1 --pd 1 --kv 1
"""

from __future__ import annotations

import pytest

from mcp_docs_tidb.common.filters import build_filter_from_arbitrary
from mcp_docs_tidb.settings import FilterableField, TiDBSettings
from mcp_docs_tidb.tidb import Entry, TiDBConnector

from tests.conftest import DeterministicEmbeddingProvider, requires_tidb

pytestmark = [requires_tidb]


def test_store_and_search_returns_closest_first(
    connector: TiDBConnector, collection_name: str, cleanup_table: list[str]
) -> None:
    cleanup_table.append(collection_name)

    connector.store(Entry(content="apple"), collection_name=collection_name)
    connector.store(Entry(content="banana"), collection_name=collection_name)
    connector.store(Entry(content="cherry"), collection_name=collection_name)

    results = connector.search("apple", collection_name=collection_name, limit=3)

    assert len(results) == 3
    # The deterministic embedder maps identical inputs to identical vectors,
    # so the exact-match entry must come first (distance ≈ 0).
    assert results[0].content == "apple"


def test_search_returns_empty_for_missing_collection(
    connector: TiDBConnector,
) -> None:
    results = connector.search(
        "anything", collection_name="mcp_test_does_not_exist", limit=3
    )
    assert results == []


def test_metadata_round_trips_through_json_column(
    connector: TiDBConnector, collection_name: str, cleanup_table: list[str]
) -> None:
    cleanup_table.append(collection_name)

    connector.store(
        Entry(content="needle", metadata={"k": "v", "n": 7, "nested": {"a": 1}}),
        collection_name=collection_name,
    )

    results = connector.search("needle", collection_name=collection_name, limit=1)

    assert len(results) == 1
    assert results[0].metadata == {"k": "v", "n": 7, "nested": {"a": 1}}


def test_collection_name_required_when_no_default(
    connector: TiDBConnector,
) -> None:
    with pytest.raises(ValueError, match="No collection name"):
        connector.store(Entry(content="x"), collection_name=None)


def test_invalid_identifier_rejected(connector: TiDBConnector) -> None:
    with pytest.raises(ValueError, match="Invalid TiDB table identifier"):
        connector.store(
            Entry(content="x"), collection_name="bad name; DROP TABLE x"
        )


def test_dict_filter_filters_rows(
    connector: TiDBConnector, collection_name: str, cleanup_table: list[str]
) -> None:
    cleanup_table.append(collection_name)

    connector.store(
        Entry(content="apple", metadata={"category": "fruit"}),
        collection_name=collection_name,
    )
    connector.store(
        Entry(content="banana", metadata={"category": "fruit"}),
        collection_name=collection_name,
    )
    connector.store(
        Entry(content="hammer", metadata={"category": "tool"}),
        collection_name=collection_name,
    )

    f = build_filter_from_arbitrary(
        {"must": [{"field": "category", "op": "==", "value": "tool"}]}
    )
    results = connector.search(
        "anything",
        collection_name=collection_name,
        limit=10,
        dict_filter=f,
    )

    assert len(results) == 1
    assert results[0].content == "hammer"


def test_dict_filter_in_operator(
    connector: TiDBConnector, collection_name: str, cleanup_table: list[str]
) -> None:
    cleanup_table.append(collection_name)

    connector.store(
        Entry(content="apple", metadata={"category": "fruit"}),
        collection_name=collection_name,
    )
    connector.store(
        Entry(content="hammer", metadata={"category": "tool"}),
        collection_name=collection_name,
    )
    connector.store(
        Entry(content="silk", metadata={"category": "cloth"}),
        collection_name=collection_name,
    )

    f = build_filter_from_arbitrary(
        {"must": [{"field": "category", "op": "in", "value": ["tool", "cloth"]}]}
    )
    results = connector.search(
        "anything",
        collection_name=collection_name,
        limit=10,
        dict_filter=f,
    )

    contents = sorted(r.content for r in results)
    assert contents == ["hammer", "silk"]


def test_use_vector_index_adds_inline_hnsw_index(
    tidb_settings: TiDBSettings,
    embedding_provider: DeterministicEmbeddingProvider,
    collection_name: str,
    cleanup_table: list[str],
) -> None:
    cleanup_table.append(collection_name)
    tidb_settings = tidb_settings.model_copy(update={"use_vector_index": True})

    connector = TiDBConnector(
        settings=tidb_settings,
        embedding_provider=embedding_provider,
    )
    try:
        connector.store(Entry(content="hello"), collection_name=collection_name)
        client = connector._get_client()
        rows = client.query(
            f"SHOW CREATE TABLE `{collection_name}`"
        ).to_list()
        create_sql = rows[0]["Create Table"].lower() if rows else ""
    finally:
        connector.close()

    assert "vector index" in create_sql
    assert "vec_cosine_distance" in create_sql


def test_use_vector_index_disabled_when_explicitly_set_false(
    tidb_settings: TiDBSettings,
    embedding_provider: DeterministicEmbeddingProvider,
    collection_name: str,
    cleanup_table: list[str],
) -> None:
    cleanup_table.append(collection_name)

    tidb_settings = tidb_settings.model_copy(update={"use_vector_index": False})
    conn = TiDBConnector(settings=tidb_settings, embedding_provider=embedding_provider)
    conn.store(Entry(content="hello"), collection_name=collection_name)

    client = conn._get_client()
    rows = client.query(f"SHOW CREATE TABLE `{collection_name}`").to_list()
    create_sql = rows[0]["Create Table"].lower() if rows else ""

    assert "vector index" not in create_sql


def test_default_collection_used_when_argument_omitted(
    tidb_settings: TiDBSettings,
    embedding_provider: DeterministicEmbeddingProvider,
    collection_name: str,
    cleanup_table: list[str],
) -> None:
    cleanup_table.append(collection_name)
    tidb_settings = tidb_settings.model_copy(
        update={"collection_name": collection_name}
    )

    connector = TiDBConnector(
        settings=tidb_settings,
        embedding_provider=embedding_provider,
    )
    try:
        connector.store(Entry(content="hello"))
        results = connector.search("hello", limit=1)
        assert len(results) == 1
        assert results[0].content == "hello"
    finally:
        connector.close()


def test_list_sources_returns_empty_for_missing_collection(
    connector: TiDBConnector,
) -> None:
    assert (
        connector.list_sources(collection_name="mcp_test_does_not_exist") == []
    )


def test_list_sources_groups_by_source(
    connector: TiDBConnector, collection_name: str, cleanup_table: list[str]
) -> None:
    cleanup_table.append(collection_name)

    connector.store(
        Entry(
            content="a-1",
            metadata={"source": "/abs/a.md", "mtime": 100.0, "ingested_at": 1.0},
        ),
        collection_name=collection_name,
    )
    connector.store(
        Entry(
            content="a-2",
            metadata={"source": "/abs/a.md", "mtime": 200.0, "ingested_at": 2.0},
        ),
        collection_name=collection_name,
    )
    connector.store(
        Entry(
            content="b-1",
            metadata={"source": "/abs/b.md", "mtime": 50.0, "ingested_at": 3.0},
        ),
        collection_name=collection_name,
    )
    # Row without a `source` key must be ignored.
    connector.store(
        Entry(content="orphan", metadata={"note": "no source"}),
        collection_name=collection_name,
    )

    rows = connector.list_sources(collection_name=collection_name)
    by_source = {r["source"]: r for r in rows}

    assert set(by_source) == {"/abs/a.md", "/abs/b.md"}
    assert by_source["/abs/a.md"]["chunks"] == 2
    assert by_source["/abs/a.md"]["mtime"] == 200.0
    assert by_source["/abs/a.md"]["ingested_at"] == 2.0
    assert by_source["/abs/b.md"]["chunks"] == 1
    assert by_source["/abs/b.md"]["mtime"] == 50.0
    assert by_source["/abs/b.md"]["ingested_at"] == 3.0


def test_filterable_fields_declared_dont_crash(
    tidb_settings: TiDBSettings,
    embedding_provider: DeterministicEmbeddingProvider,
    collection_name: str,
    cleanup_table: list[str],
) -> None:
    """
    Filterable fields used to drive VIRTUAL-column DDL. After the switch
    to pytidb's JSON-path filter DSL the declarations are still accepted
    (they only shape the tool argument surface) but no DDL changes
    happen — this regression test pins that behaviour.
    """
    cleanup_table.append(collection_name)
    connector = TiDBConnector(
        settings=tidb_settings,
        embedding_provider=embedding_provider,
        filterable_fields={
            "category": FilterableField(
                name="category",
                description="category",
                field_type="keyword",
                condition="==",
            ),
        },
    )
    try:
        connector.store(
            Entry(content="x", metadata={"category": "work"}),
            collection_name=collection_name,
        )
        results = connector.search(
            "x",
            collection_name=collection_name,
            limit=10,
            dict_filter={"metadata.category": "work"},
        )
        assert len(results) == 1
    finally:
        connector.close()
