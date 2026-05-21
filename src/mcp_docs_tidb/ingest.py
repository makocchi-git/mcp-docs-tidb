"""
Bulk-ingest local files into a TiDB collection.

Exposed both as a Python API (`ingest_paths`) and as a CLI script
(`mcp-docs-tidb-ingest`) wired up in pyproject.toml. The CLI lets you
populate the table that the MCP server will later read from, without
going through an LLM.

Re-ingest semantics: each ingested chunk is tagged with `metadata.source`
set to the absolute file path. When `replace=True` (the default) we
`DELETE` every existing row sharing the same `source` before writing the
new chunks. That gives idempotent, file-grained updates.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable

from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    TiDBSettings,
)
from mcp_docs_tidb.tidb import Entry, TiDBConnector

logger = logging.getLogger(__name__)


def chunk_text(
    text: str, max_chars: int = 2000, overlap: int = 200
) -> list[str]:
    """
    Split `text` into chunks of at most `max_chars` characters, each
    overlapping the previous one by `overlap` characters. Whitespace-only
    output chunks are skipped.

    Character-based (not token-based) chunking is intentionally coarse —
    it has no dependency beyond the standard library, and the FastEmbed
    models accept inputs well past typical chunk sizes.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must satisfy 0 <= overlap < max_chars")

    chunks: list[str] = []
    n = len(text)
    if n == 0:
        return chunks

    start = 0
    while start < n:
        end = min(start + max_chars, n)
        piece = text[start:end]
        if piece.strip():
            chunks.append(piece)
        if end >= n:
            break
        start = end - overlap
    return chunks


def collect_paths(
    paths: Iterable[str | Path],
    *,
    recursive: bool = False,
    glob: str = "*",
) -> list[Path]:
    """
    Expand a mix of file and directory paths into a sorted list of files.
    Directories are expanded with `glob` (using `rglob` if `recursive`).
    """
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob(glob) if recursive else p.glob(glob)
            out.extend(sorted(x for x in it if x.is_file()))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(str(p))
    return out


def ingest_paths(
    paths: Iterable[Path],
    *,
    collection_name: str,
    connector: TiDBConnector,
    chunk_chars: int = 2000,
    overlap: int = 200,
    replace: bool = True,
    extra_metadata: dict | None = None,
) -> int:
    """
    Read each file in `paths`, chunk its contents, and insert each chunk
    into `collection_name`. Returns the total number of chunks written.

    When `replace=True` (default), any pre-existing chunks tagged with the
    same `metadata.source` are deleted first — so calling this twice with
    the same file leaves the table in the same state as calling it once.
    """
    total = 0
    for path in paths:
        text = path.read_text(encoding="utf-8")
        source = str(path.resolve())

        if replace:
            removed = connector.delete_by_metadata_field(
                collection_name=collection_name,
                field_name="source",
                field_value=source,
            )
            if removed:
                logger.info("Removed %d stale chunk(s) for %s", removed, source)

        chunks = chunk_text(text, max_chars=chunk_chars, overlap=overlap)
        for i, chunk in enumerate(chunks):
            metadata: dict = {"source": source, "chunk": i}
            if extra_metadata:
                metadata.update(extra_metadata)
            connector.store(
                Entry(content=chunk, metadata=metadata),
                collection_name=collection_name,
            )
            total += 1
        logger.info("Ingested %d chunk(s) from %s", len(chunks), source)

    return total


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-docs-tidb-ingest",
        description="Bulk-ingest files into a TiDB MCP collection.",
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to ingest.",
    )
    parser.add_argument(
        "--collection",
        required=True,
        help="Target TiDB table name (auto-created if missing).",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=2000,
        help="Max characters per chunk (default: 2000).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
        help="Overlap in characters between adjacent chunks (default: 200).",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="Recurse into directories.",
    )
    parser.add_argument(
        "--glob",
        default="*.md",
        help="Glob pattern applied when expanding directories (default: *.md).",
    )
    parser.add_argument(
        "--no-replace",
        dest="replace",
        action="store_false",
        help=(
            "Do not delete existing chunks for the same source first. "
            "By default re-ingesting the same file replaces its prior chunks."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging.",
    )
    return parser


def _run_cli(args: argparse.Namespace) -> int:
    files = collect_paths(args.paths, recursive=args.recursive, glob=args.glob)
    if not files:
        print("No files matched.", file=sys.stderr)
        return 1

    embedding_provider: EmbeddingProvider = create_embedding_provider(
        EmbeddingProviderSettings()
    )
    connector = TiDBConnector(
        settings=TiDBSettings(),
        embedding_provider=embedding_provider,
    )
    try:
        count = ingest_paths(
            files,
            collection_name=args.collection,
            connector=connector,
            chunk_chars=args.chunk_chars,
            overlap=args.overlap,
            replace=args.replace,
        )
    finally:
        connector.close()

    print(
        f"Ingested {count} chunk(s) from {len(files)} file(s) "
        f"into '{args.collection}'."
    )
    return 0


def main() -> None:
    parser = _build_argparser()
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname) s %(message) s",
    )
    sys.exit(_run_cli(args))


if __name__ == "__main__":
    main()
