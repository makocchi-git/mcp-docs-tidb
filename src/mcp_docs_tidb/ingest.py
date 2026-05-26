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
import fnmatch
import json
import logging
import sys
import time
from pathlib import Path
from typing import Iterable

from sqlalchemy.exc import SQLAlchemyError

from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    TiDBSettings,
)
from mcp_docs_tidb.tidb import Entry, TiDBConnector, format_db_error

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


def _is_excluded(path: Path, exclude_globs: list[str]) -> bool:
    """Return True if path matches any of the exclude glob patterns.

    Each pattern is matched against both the filename and the full path string
    so that e.g. 'CHANGELOG.md' and '*/drafts/*' both work as expected.
    """
    name = path.name
    full = str(path)
    return any(
        fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(full, pat)
        for pat in exclude_globs
    )


def collect_paths(
    paths: Iterable[str | Path],
    *,
    recursive: bool = False,
    glob: str = "*",
    exclude_globs: list[str] | None = None,
) -> list[Path]:
    """
    Expand a mix of file and directory paths into a sorted list of files.
    Directories are expanded with `glob` (using `rglob` if `recursive`).
    Files matching any pattern in `exclude_globs` are omitted.
    """
    excludes = exclude_globs or []
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob(glob) if recursive else p.glob(glob)
            out.extend(
                sorted(
                    x for x in it
                    if x.is_file() and not _is_excluded(x, excludes)
                )
            )
        elif p.is_file():
            if not _is_excluded(p, excludes):
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
    only_modified: bool = False,
    truncate_collection: bool = False,
    extra_metadata: dict | None = None,
) -> int:
    """
    Read each file in `paths`, chunk its contents, and insert each chunk
    into `collection_name`. Returns the total number of chunks written.

    When `replace=True` (default), any pre-existing chunks tagged with the
    same `metadata.source` are deleted first — so calling this twice with
    the same file leaves the table in the same state as calling it once.

    When `only_modified=True`, each file is compared against the largest
    `metadata.mtime` already recorded for the same `metadata.source`; files
    whose on-disk mtime is not strictly greater are skipped entirely.
    Files with no prior record are always processed.

    When `truncate_collection=True`, every row of `collection_name` is
    removed via ``TRUNCATE TABLE`` *before* any file is processed. The
    table schema is kept. This is the right knob for "rebuild from
    scratch" runs; combining it with `only_modified=True` is allowed but
    pointless — after the truncate there is no prior `mtime` to compare
    against, so every file is processed.
    """
    if truncate_collection:
        truncated = connector.truncate_collection(collection_name=collection_name)
        if truncated:
            logger.info("Truncated collection %s", collection_name)

    total = 0
    for path in paths:
        source = str(path.resolve())

        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            logger.warning("Skipping %s: cannot stat file: %s", source, exc)
            continue

        if only_modified:
            prev = connector.get_max_numeric_metadata_value(
                collection_name=collection_name,
                match_field="source",
                match_value=source,
                value_field="mtime",
            )
            if prev is not None and mtime <= prev:
                logger.info(
                    "Skipping %s (mtime %.6f <= recorded %.6f)",
                    source,
                    mtime,
                    prev,
                )
                continue

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Skipping %s: cannot read file: %s", source, exc)
            continue
        except UnicodeDecodeError as exc:
            logger.warning("Skipping %s: encoding error: %s", source, exc)
            continue

        # TOCTOU: re-check mtime after read; adopt post-read value if it drifted
        try:
            post_mtime = path.stat().st_mtime
            if abs(post_mtime - mtime) >= 1.0:
                logger.warning(
                    "File %s was modified during read (pre=%.6f post=%.6f); "
                    "using post-read mtime",
                    source,
                    mtime,
                    post_mtime,
                )
                mtime = post_mtime
        except OSError:
            pass  # File may have been deleted after read; use original mtime

        ingested_at = time.time()

        if replace:
            removed = connector.delete_by_metadata_field(
                collection_name=collection_name,
                field_name="source",
                field_value=source,
            )
            if removed:
                logger.info("Removed %d stale chunk(s) for %s", removed, source)

        chunks = chunk_text(text, max_chars=chunk_chars, overlap=overlap)
        stored_count = 0
        try:
            for i, chunk in enumerate(chunks):
                metadata: dict = dict(extra_metadata) if extra_metadata else {}
                metadata.update(
                    {
                        "source": source,
                        "chunk": i,
                        "mtime": mtime,
                        "ingested_at": ingested_at,
                    }
                )
                connector.store(
                    Entry(content=chunk, metadata=metadata),
                    collection_name=collection_name,
                )
                stored_count += 1
        except Exception:
            if stored_count > 0:
                # Compensate: remove partially stored chunks for this source
                try:
                    connector.delete_by_metadata_field(
                        collection_name=collection_name,
                        field_name="source",
                        field_value=source,
                    )
                    logger.warning(
                        "Rolled back %d partial chunk(s) for %s after store failure",
                        stored_count,
                        source,
                    )
                except Exception as del_exc:
                    logger.warning(
                        "Failed to roll back partial chunks for %s: %s",
                        source,
                        del_exc,
                    )
            raise
        total += stored_count
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
        "--only-modified",
        dest="only_modified",
        action="store_true",
        help=(
            "Skip files whose on-disk mtime is not newer than the "
            "metadata.mtime already stored for the same source. Files with "
            "no prior record are still processed."
        ),
    )
    parser.add_argument(
        "--truncate",
        dest="truncate_collection",
        action="store_true",
        help=(
            "TRUNCATE the target table before ingesting (table schema is "
            "kept). Use this to rebuild a collection from scratch — every "
            "input file is then re-chunked and re-embedded."
        ),
    )
    parser.add_argument(
        "--exclude-glob",
        dest="exclude_globs",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Glob pattern for files to exclude. May be specified multiple times. "
            "Matched against both the filename and the full path "
            "(e.g. --exclude-glob 'CHANGELOG.md' --exclude-glob '*/drafts/*')."
        ),
    )
    parser.add_argument(
        "--extra-metadata",
        dest="extra_metadata",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Extra metadata key=value pair attached to every ingested chunk. "
            "May be specified multiple times "
            "(e.g. --extra-metadata category=docs --extra-metadata version=1.0). "
            "Values that are valid JSON (numbers, booleans, arrays, objects) are "
            "decoded automatically; others are stored as strings."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging.",
    )
    return parser


def _parse_extra_metadata_args(pairs: list[str]) -> dict:
    """Convert a list of KEY=VALUE strings into a metadata dict.

    Values are decoded as JSON when possible so that numbers and booleans
    round-trip correctly; anything that is not valid JSON is kept as a string.

    Raises ValueError if a pair does not contain '='.
    """
    result: dict = {}
    for kv in pairs:
        if "=" not in kv:
            raise ValueError(
                f"--extra-metadata must be KEY=VALUE, got: {kv!r}"
            )
        key, _, raw = kv.partition("=")
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result


def _run_cli(args: argparse.Namespace) -> int:
    files = collect_paths(
        args.paths,
        recursive=args.recursive,
        glob=args.glob,
        exclude_globs=args.exclude_globs,
    )
    if not files:
        print("No files matched.", file=sys.stderr)
        return 1

    try:
        extra_metadata = _parse_extra_metadata_args(args.extra_metadata) or None
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    embedding_provider: EmbeddingProvider = create_embedding_provider(
        EmbeddingProviderSettings()
    )
    tidb_settings = TiDBSettings()
    connector = TiDBConnector(
        settings=tidb_settings,
        embedding_provider=embedding_provider,
    )
    try:
        try:
            count = ingest_paths(
                files,
                collection_name=args.collection,
                connector=connector,
                chunk_chars=args.chunk_chars,
                overlap=args.overlap,
                replace=args.replace,
                only_modified=args.only_modified,
                truncate_collection=args.truncate_collection,
                extra_metadata=extra_metadata,
            )
        except (SQLAlchemyError, OSError) as exc:
            print(format_db_error(exc, tidb_settings), file=sys.stderr)
            if args.verbose:
                # Re-raise so the user gets the full traceback when they
                # asked for verbose output, but only after the friendly
                # message has been printed.
                raise
            return 2
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
    if not args.verbose:
        # pytidb logs a redundant ERROR line before re-raising connection
        # failures; silence it so the only thing the user sees on a bad
        # connection is our formatted message. `-v` restores the noise.
        logging.getLogger("pytidb").setLevel(logging.CRITICAL)
    sys.exit(_run_cli(args))


if __name__ == "__main__":
    main()
