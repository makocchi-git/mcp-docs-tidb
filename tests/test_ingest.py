"""
Tests for `mcp_docs_tidb.ingest`. The chunking helper is exercised as a
unit test; the file-driven `ingest_paths` is run against the live TiDB and
verifies the "replace on re-ingest" semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from mcp_docs_tidb.ingest import chunk_text, collect_paths, ingest_paths
from mcp_docs_tidb.tidb import Entry, TiDBConnector

from tests.conftest import requires_tidb


class _RecordingConnector:
    """
    Minimal stub matching the duck-typed surface `ingest_paths` uses:
    `delete_by_metadata_field(...)`, `get_max_numeric_metadata_value(...)`,
    and `store(entry, *, collection_name)`.
    """

    def __init__(self, recorded_mtimes: dict[str, float] | None = None) -> None:
        self.stored: list[tuple[Entry, str | None]] = []
        self._recorded_mtimes = recorded_mtimes or {}
        self.truncate_calls: list[str | None] = []

    def delete_by_metadata_field(
        self, *, collection_name: str | None, field_name: str, field_value: Any
    ) -> int:
        return 0

    def truncate_collection(self, *, collection_name: str | None) -> bool:
        self.truncate_calls.append(collection_name)
        # Pretend the table existed.
        self._recorded_mtimes.clear()
        return True

    def get_max_numeric_metadata_value(
        self,
        *,
        collection_name: str | None,
        match_field: str,
        match_value: Any,
        value_field: str,
    ) -> float | None:
        if match_field == "source" and value_field == "mtime":
            return self._recorded_mtimes.get(match_value)
        return None

    def store(self, entry: Entry, *, collection_name: str | None = None) -> None:
        self.stored.append((entry, collection_name))


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


def test_collect_paths_exclude_by_filename(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "CHANGELOG.md").write_text("changelog")
    files = collect_paths([tmp_path], glob="*.md", exclude_globs=["CHANGELOG.md"])
    assert [p.name for p in files] == ["a.md"]


def test_collect_paths_exclude_by_path_pattern(tmp_path: Path) -> None:
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (tmp_path / "good.md").write_text("good")
    (drafts / "draft.md").write_text("draft")
    files = collect_paths(
        [tmp_path], recursive=True, glob="*.md", exclude_globs=["*/drafts/*"]
    )
    assert [p.name for p in files] == ["good.md"]


def test_collect_paths_exclude_multiple_patterns(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    (tmp_path / "c.md").write_text("c")
    files = collect_paths(
        [tmp_path], glob="*.md", exclude_globs=["b.md", "c.md"]
    )
    assert [p.name for p in files] == ["a.md"]


def test_collect_paths_exclude_individual_file(tmp_path: Path) -> None:
    keep = tmp_path / "keep.md"
    skip = tmp_path / "skip.md"
    keep.write_text("keep")
    skip.write_text("skip")
    files = collect_paths([keep, skip], exclude_globs=["skip.md"])
    assert files == [keep]


def test_collect_paths_exclude_empty_list_keeps_all(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    files = collect_paths([tmp_path], glob="*.md", exclude_globs=[])
    assert [p.name for p in files] == ["a.md", "b.md"]


# ---------------------------------------------------------------------------
# ingest_paths — metadata (unit tests with a stub connector)
# ---------------------------------------------------------------------------


def test_ingest_paths_records_mtime_and_ingested_at(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("alpha beta gamma")
    expected_mtime = f.stat().st_mtime

    stub = _RecordingConnector()
    written = ingest_paths(
        [f],
        collection_name="any",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
    )

    assert written == 1
    assert len(stub.stored) == 1
    entry, _ = stub.stored[0]
    assert entry.metadata is not None
    assert entry.metadata["source"] == str(f.resolve())
    assert entry.metadata["chunk"] == 0
    assert entry.metadata["mtime"] == expected_mtime
    assert isinstance(entry.metadata["ingested_at"], float)
    assert entry.metadata["ingested_at"] >= expected_mtime


def test_only_modified_skips_file_when_mtime_not_newer(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("payload")
    source = str(f.resolve())
    current = f.stat().st_mtime

    stub = _RecordingConnector(recorded_mtimes={source: current})
    written = ingest_paths(
        [f],
        collection_name="any",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
        only_modified=True,
    )

    assert written == 0
    assert stub.stored == []


def test_only_modified_processes_when_file_is_newer(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("payload")
    source = str(f.resolve())
    current = f.stat().st_mtime

    stub = _RecordingConnector(recorded_mtimes={source: current - 10.0})
    written = ingest_paths(
        [f],
        collection_name="any",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
        only_modified=True,
    )

    assert written == 1
    assert len(stub.stored) == 1


def test_truncate_collection_calls_connector(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("payload")

    stub = _RecordingConnector()
    ingest_paths(
        [f],
        collection_name="kb",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
        truncate_collection=True,
    )

    assert stub.truncate_calls == ["kb"]
    assert len(stub.stored) == 1


def test_truncate_overrides_only_modified(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("payload")
    source = str(f.resolve())
    current = f.stat().st_mtime

    # Even with a recorded mtime that would normally cause `only_modified`
    # to skip the file, the truncate clears the history first and so the
    # file ends up being processed.
    stub = _RecordingConnector(recorded_mtimes={source: current})
    written = ingest_paths(
        [f],
        collection_name="kb",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
        only_modified=True,
        truncate_collection=True,
    )

    assert stub.truncate_calls == ["kb"]
    assert written == 1


def test_only_modified_processes_new_source(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("payload")

    stub = _RecordingConnector()  # no prior records
    written = ingest_paths(
        [f],
        collection_name="any",
        connector=cast(TiDBConnector, stub),
        chunk_chars=64,
        overlap=0,
        only_modified=True,
    )

    assert written == 1
    assert len(stub.stored) == 1


def test_ingest_paths_mtime_constant_across_chunks(tmp_path: Path) -> None:
    f = tmp_path / "doc.md"
    f.write_text("abcdefghij" * 5)  # 50 chars -> multiple chunks
    expected_mtime = f.stat().st_mtime

    stub = _RecordingConnector()
    ingest_paths(
        [f],
        collection_name="any",
        connector=cast(TiDBConnector, stub),
        chunk_chars=10,
        overlap=0,
    )

    assert len(stub.stored) > 1
    mtimes = {e.metadata["mtime"] for e, _ in stub.stored if e.metadata}
    ingested = {e.metadata["ingested_at"] for e, _ in stub.stored if e.metadata}
    assert mtimes == {expected_mtime}
    # All chunks of one file share a single ingested_at stamp.
    assert len(ingested) == 1


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


@requires_tidb
def test_only_modified_skips_unchanged_file(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    f = tmp_path / "doc.md"
    f.write_text("payload one")

    first = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )
    assert first == 1

    # Rewrite content but force mtime back to its original value so the
    # only_modified comparison decides "not newer".
    original_mtime = f.stat().st_mtime
    f.write_text("payload one and a half")
    import os

    os.utime(f, (original_mtime, original_mtime))

    second = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
        only_modified=True,
    )
    assert second == 0

    client = connector._get_client()
    rows = client.query(f"SELECT content FROM `{collection_name}`").to_list()
    contents = {r["content"] for r in rows}
    assert contents == {"payload one"}


@requires_tidb
def test_truncate_collection_wipes_existing_rows(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    keep = tmp_path / "old.md"
    keep.write_text("legacy content")
    ingest_paths(
        [keep],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )

    new = tmp_path / "new.md"
    new.write_text("fresh content")
    written = ingest_paths(
        [new],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
        truncate_collection=True,
    )

    assert written == 1
    client = connector._get_client()
    rows = client.query(f"SELECT content FROM `{collection_name}`").to_list()
    contents = {r["content"] for r in rows}
    assert contents == {"fresh content"}


@requires_tidb
def test_truncate_collection_noop_when_table_missing(
    connector: TiDBConnector,
    collection_name: str,
) -> None:
    # Table does not exist yet; truncate must not raise. No cleanup_table
    # registration here — pytidb's drop_table errors on a missing table
    # even with if_not_exists="skip".
    assert (
        connector.truncate_collection(collection_name=collection_name) is False
    )


@requires_tidb
def test_only_modified_processes_when_mtime_advances(
    connector: TiDBConnector,
    collection_name: str,
    cleanup_table: list[str],
    tmp_path: Path,
) -> None:
    cleanup_table.append(collection_name)

    f = tmp_path / "doc.md"
    f.write_text("payload one")
    import os

    base = f.stat().st_mtime
    os.utime(f, (base, base))

    ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
    )

    f.write_text("payload two")
    os.utime(f, (base + 60.0, base + 60.0))

    written = ingest_paths(
        [f],
        collection_name=collection_name,
        connector=connector,
        chunk_chars=64,
        overlap=0,
        only_modified=True,
    )
    assert written == 1

    client = connector._get_client()
    rows = client.query(f"SELECT content FROM `{collection_name}`").to_list()
    contents = {r["content"] for r in rows}
    assert contents == {"payload two"}
