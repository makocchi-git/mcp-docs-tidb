"""
Tests for `mcp_docs_tidb.ingest`. The chunking helper is exercised as a
unit test; the file-driven `ingest_paths` is run against the live TiDB and
verifies the "replace on re-ingest" semantics.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_docs_tidb.ingest import chunk_text, collect_paths, ingest_paths
from mcp_docs_tidb.tidb import TiDBConnector

from tests.conftest import requires_tidb


# ---------------------------------------------------------------------------
# chunk_text — pure logic
# ---------------------------------------------------------------------------


def test_chunk_text_short_input_returns_single_chunk() -> None:
    chunks = chunk_text("hello", max_chars=100, overlap=10)
    assert chunks == ["hello"]


def test_chunk_text_splits_with_overlap() -> None:
    text = "abcdefghij"  # 10 chars
    chunks = chunk_text(text, max_chars=4, overlap=1)
    # 0:4 -> "abcd", next start = 4-1 = 3, 3:7 -> "defg",
    # next start = 7-1 = 6, 6:10 -> "ghij"
    assert chunks == ["abcd", "defg", "ghij"]


def test_chunk_text_zero_overlap_is_disjoint() -> None:
    text = "abcdefgh"
    chunks = chunk_text(text, max_chars=3, overlap=0)
    assert chunks == ["abc", "def", "gh"]


def test_chunk_text_skips_whitespace_only_chunks() -> None:
    chunks = chunk_text("   ", max_chars=10, overlap=0)
    assert chunks == []


def test_chunk_text_empty_input_returns_empty() -> None:
    assert chunk_text("", max_chars=10, overlap=0) == []


def test_chunk_text_invalid_overlap_raises() -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=4, overlap=4)
    with pytest.raises(ValueError):
        chunk_text("abc", max_chars=0, overlap=0)


# ---------------------------------------------------------------------------
# collect_paths — pure logic
# ---------------------------------------------------------------------------


def test_collect_paths_expands_directory(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    files = collect_paths([tmp_path], recursive=False, glob="*.md")
    assert [p.name for p in files] == ["a.md", "b.md"]


def test_collect_paths_recursive(tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "deep.md").write_text("x")
    (tmp_path / "top.md").write_text("y")
    files = collect_paths([tmp_path], recursive=True, glob="*.md")
    assert sorted(p.name for p in files) == ["deep.md", "top.md"]


def test_collect_paths_accepts_individual_files(tmp_path: Path) -> None:
    f = tmp_path / "one.md"
    f.write_text("x")
    files = collect_paths([f])
    assert files == [f]


def test_collect_paths_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        collect_paths([tmp_path / "nope.md"])


# ---------------------------------------------------------------------------
# ingest_paths — integration
# ---------------------------------------------------------------------------


@requires_tidb
def test_ingest_paths_writes_chunks(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    f = tmp_path / "doc.md"
    f.write_text("alpha beta gamma delta epsilon zeta")

    written = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=10,
        overlap=2,
    )

    assert written >= 3

    results = connector.search("alpha", collection_name=collection_name, limit=10)
    assert results, "expected at least one chunk back"
    sources = {(r.metadata or {}).get("source") for r in results}
    assert sources == {str(f.resolve())}


@requires_tidb
def test_reingest_replaces_old_chunks(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    f = tmp_path / "doc.md"
    f.write_text("original version of the document")

    first = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )

    f.write_text("rewritten payload completely different")

    second = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )

    assert first == 1
    assert second == 1

    client = connector._get_client()
    rows = client.query(f"SELECT content FROM `{collection_name}`").to_list()
    contents = {r["content"] for r in rows}
    assert contents == {"rewritten payload completely different"}


@requires_tidb
def test_reingest_with_no_replace_appends(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    f = tmp_path / "doc.md"
    f.write_text("payload one")

    ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )
    f.write_text("payload two")
    ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
        replace=False,
    )

    client = connector._get_client()
    rows = client.query(f"SELECT content FROM `{collection_name}`").to_list()
    contents = {r["content"] for r in rows}
    assert contents == {"payload one", "payload two"}


@requires_tidb
def test_delete_by_metadata_field_rejects_bad_name(
    connector: TiDBConnector,
) -> None:
    with pytest.raises(ValueError, match="Invalid metadata field name"):
        connector.delete_by_metadata_field(
            collection_name="any",
            field_name="bad name",
            field_value="x",
        )
