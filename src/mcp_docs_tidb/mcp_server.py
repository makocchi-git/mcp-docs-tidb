import json
import logging
import time
from typing import Annotated, Any, Optional

from fastmcp import Context, FastMCP
from pydantic import Field
from sqlalchemy.exc import SQLAlchemyError

from pathlib import Path

from mcp_docs_tidb.common.filters import build_filter_from_arbitrary
from mcp_docs_tidb.common.func_tools import make_partial_function
from mcp_docs_tidb.common.wrap_filters import wrap_filters
from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.ingest import collect_paths, ingest_paths
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    TiDBSettings,
    ToolSettings,
)
from mcp_docs_tidb.tidb import (
    ArbitraryFilter,
    Entry,
    Metadata,
    PyTiDBFilter,
    TiDBConnector,
    format_db_error,
)

logger = logging.getLogger(__name__)


class TiDBMCPServer(FastMCP):
    """
    A MCP server backed by TiDB's VECTOR type and VEC_COSINE_DISTANCE.
    """

    def __init__(
        self,
        tool_settings: ToolSettings,
        tidb_settings: TiDBSettings,
        embedding_provider_settings: Optional[EmbeddingProviderSettings] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        name: str = "mcp-docs-tidb",
        instructions: str | None = None,
        **settings: Any,
    ):
        self.tool_settings = tool_settings
        self.tidb_settings = tidb_settings

        if embedding_provider_settings and embedding_provider:
            raise ValueError(
                "Cannot provide both embedding_provider_settings and embedding_provider"
            )
        if not embedding_provider_settings and not embedding_provider:
            raise ValueError(
                "Must provide either embedding_provider_settings or embedding_provider"
            )

        if embedding_provider_settings is not None:
            self.embedding_provider_settings: Optional[EmbeddingProviderSettings] = (
                embedding_provider_settings
            )
            self.embedding_provider: EmbeddingProvider = create_embedding_provider(
                embedding_provider_settings
            )
        else:
            self.embedding_provider_settings = None
            assert embedding_provider is not None
            self.embedding_provider = embedding_provider

        self.tidb_connector = TiDBConnector(
            settings=tidb_settings,
            embedding_provider=self.embedding_provider,
            filterable_fields=tidb_settings.filterable_fields_dict(),
        )

        # Instance attributes above must be assigned before super().__init__()
        # because FastMCP's metaclass runs immediately and setup_tools() reads
        # them. setup_tools() itself is called after super().__init__() so that
        # FastMCP is fully initialised before self.tool() is invoked.
        super().__init__(name=name, instructions=instructions, **settings)

        self.setup_tools()

    def format_entry(self, entry: Entry) -> str:
        metadata_json = json.dumps(entry.metadata) if entry.metadata else ""
        return (
            f"<entry><content>{entry.content}</content>"
            f"<metadata>{metadata_json}</metadata></entry>"
        )

    def setup_tools(self) -> None:
        async def store(
            ctx: Context,
            information: Annotated[str, Field(description="Text to store")],
            collection_name: Annotated[
                str, Field(description="The collection (TiDB table) to store into")
            ],
            metadata: Annotated[
                Metadata | None,
                Field(
                    description="Extra JSON metadata stored alongside the information."
                ),
            ] = None,
            mtime: Annotated[
                Optional[float],
                Field(
                    description=(
                        "Source modification time as a Unix epoch (seconds). "
                        "Stored under metadata.mtime for freshness filtering / "
                        "incremental re-ingest. Takes precedence over any "
                        "`mtime` already present in `metadata`."
                    )
                ),
            ] = None,
        ) -> str:
            await ctx.debug(f"Storing information in TiDB table {collection_name}")
            merged_metadata: Metadata = dict(metadata) if metadata else {}
            if mtime is not None:
                merged_metadata["mtime"] = mtime
            merged_metadata["ingested_at"] = time.time()
            entry = Entry(content=information, metadata=merged_metadata)
            try:
                self.tidb_connector.store(entry, collection_name=collection_name)
            except ValueError as exc:
                logger.warning("docs-tidb-store rejected invalid input: %s", exc)
                return f"Error: {exc}"
            except (SQLAlchemyError, OSError) as exc:
                logger.exception("docs-tidb-store failed (DB)")
                return format_db_error(exc, self.tidb_settings)
            except Exception as exc:  # noqa: BLE001
                logger.exception("docs-tidb-store failed (unexpected)")
                return format_db_error(exc, self.tidb_settings)
            return f"Remembered: {information} in collection {collection_name}"

        async def find(
            ctx: Context,
            query: Annotated[str, Field(description="What to search for")],
            collection_name: Annotated[
                str, Field(description="The collection (TiDB table) to search")
            ],
            query_filter: ArbitraryFilter | None = None,
            dict_filter: PyTiDBFilter | None = None,
        ) -> list[str] | None:
            await ctx.debug(
                f"Searching TiDB table {collection_name} with filter={dict_filter!r}"
            )
            try:
                # When a wrap_filters wrapper is in front of this function, it
                # supplies dict_filter directly. Otherwise we may receive the
                # JSON-shaped `query_filter` and translate it here.
                if dict_filter is None and query_filter is not None:
                    dict_filter = build_filter_from_arbitrary(query_filter)
                entries = self.tidb_connector.search(
                    query,
                    collection_name=collection_name,
                    limit=self.tidb_settings.search_limit,
                    dict_filter=dict_filter,
                )
            except ValueError as exc:
                logger.warning("docs-tidb-find rejected invalid input: %s", exc)
                return [f"Error: {exc}"]
            except (SQLAlchemyError, OSError) as exc:
                logger.exception("docs-tidb-find failed (DB)")
                return [format_db_error(exc, self.tidb_settings)]
            except Exception as exc:  # noqa: BLE001
                logger.exception("docs-tidb-find failed (unexpected)")
                return [format_db_error(exc, self.tidb_settings)]
            if not entries:
                return None
            content = [f"Results for the query '{query}'"]
            content.extend(self.format_entry(e) for e in entries)
            return content

        async def ingest(
            ctx: Context,
            paths: Annotated[
                list[str],
                Field(
                    description=(
                        "Files or directories on the server host to ingest. "
                        "Directories are expanded with the `glob` argument."
                    )
                ),
            ],
            collection_name: Annotated[
                str,
                Field(description="The collection (TiDB table) to ingest into"),
            ],
            recursive: Annotated[
                bool,
                Field(description="Recurse into directories when expanding paths."),
            ] = False,
            glob: Annotated[
                str,
                Field(
                    description=(
                        "Glob applied when a path is a directory (e.g. '*.md')."
                    )
                ),
            ] = "*.md",
            chunk_chars: Annotated[
                int,
                Field(description="Max characters per chunk before embedding."),
            ] = 2000,
            overlap: Annotated[
                int,
                Field(description="Overlap (in characters) between adjacent chunks."),
            ] = 200,
            replace: Annotated[
                bool,
                Field(
                    description=(
                        "If true, existing chunks tagged with the same source "
                        "file are deleted first. If false, new chunks are appended."
                    )
                ),
            ] = True,
            only_modified: Annotated[
                bool,
                Field(
                    description=(
                        "If true, skip files whose on-disk mtime is not newer "
                        "than the metadata.mtime already stored for the same "
                        "source. Files with no prior record are still processed."
                    )
                ),
            ] = False,
            truncate_collection: Annotated[
                bool,
                Field(
                    description=(
                        "If true, TRUNCATE the target table before ingesting "
                        "(schema is kept). Use this to rebuild a collection "
                        "from scratch."
                    )
                ),
            ] = False,
        ) -> str:
            await ctx.debug(
                f"Ingesting {len(paths)} input(s) into {collection_name} "
                f"(recursive={recursive}, glob={glob!r}, replace={replace}, "
                f"only_modified={only_modified}, "
                f"truncate_collection={truncate_collection})"
            )
            tool_settings = self.tool_settings
            if len(paths) > tool_settings.ingest_max_paths:
                return (
                    f"Error: too many paths ({len(paths)}); "
                    f"limit is {tool_settings.ingest_max_paths}."
                )
            try:
                resolved_paths = [Path(p) for p in paths]
                if tool_settings.ingest_root:
                    root = Path(tool_settings.ingest_root).resolve()
                    for p in resolved_paths:
                        try:
                            p.resolve().relative_to(root)
                        except ValueError:
                            return f"Error: path {p!r} is outside the allowed root {str(root)!r}."
                files = collect_paths(resolved_paths, recursive=recursive, glob=glob)
                if not files:
                    return "No files matched."
                written = ingest_paths(
                    files,
                    collection_name=collection_name,
                    connector=self.tidb_connector,
                    chunk_chars=chunk_chars,
                    overlap=overlap,
                    replace=replace,
                    only_modified=only_modified,
                    truncate_collection=truncate_collection,
                )
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("docs-tidb-ingest rejected invalid input: %s", exc)
                return f"Error: {exc}"
            except (SQLAlchemyError, OSError) as exc:
                logger.exception("docs-tidb-ingest failed (DB)")
                return format_db_error(exc, self.tidb_settings)
            except Exception as exc:  # noqa: BLE001
                logger.exception("docs-tidb-ingest failed (unexpected)")
                return format_db_error(exc, self.tidb_settings)
            return (
                f"Ingested {written} chunk(s) from {len(files)} file(s) "
                f"into collection {collection_name}."
            )

        async def list_sources(
            ctx: Context,
            collection_name: Annotated[
                str,
                Field(description="The collection (TiDB table) to inspect"),
            ],
        ) -> list[dict[str, Any]] | str:
            await ctx.debug(f"Listing sources in TiDB table {collection_name}")
            try:
                return self.tidb_connector.list_sources(
                    collection_name=collection_name
                )
            except ValueError as exc:
                logger.warning("docs-tidb-list rejected invalid input: %s", exc)
                return f"Error: {exc}"
            except (SQLAlchemyError, OSError) as exc:
                logger.exception("docs-tidb-list failed (DB)")
                return format_db_error(exc, self.tidb_settings)
            except Exception as exc:  # noqa: BLE001
                logger.exception("docs-tidb-list failed (unexpected)")
                return format_db_error(exc, self.tidb_settings)

        find_foo = find
        store_foo = store
        ingest_foo = ingest
        list_foo = list_sources

        filterable_conditions = (
            self.tidb_settings.filterable_fields_dict_with_conditions()
        )

        # Three-way filter-surface dispatch based on configuration:
        #   1. filterable_fields declared with conditions → replace query_filter
        #      with one typed argument per field (wrap_filters).
        #   2. allow_arbitrary_filter=False (default) → no filter UI at all;
        #      both filter arguments are hidden from the MCP tool schema.
        #   3. allow_arbitrary_filter=True → expose the generic query_filter
        #      dict but hide the lower-level dict_filter (internal plumbing).
        if len(filterable_conditions) > 0:
            find_foo = wrap_filters(find_foo, filterable_conditions)
        elif not self.tidb_settings.allow_arbitrary_filter:
            find_foo = make_partial_function(
                find_foo, {"query_filter": None, "dict_filter": None}
            )
        else:
            find_foo = make_partial_function(find_foo, {"dict_filter": None})

        if self.tidb_settings.collection_name:
            find_foo = make_partial_function(
                find_foo, {"collection_name": self.tidb_settings.collection_name}
            )
            store_foo = make_partial_function(
                store_foo, {"collection_name": self.tidb_settings.collection_name}
            )
            ingest_foo = make_partial_function(
                ingest_foo, {"collection_name": self.tidb_settings.collection_name}
            )
            list_foo = make_partial_function(
                list_foo, {"collection_name": self.tidb_settings.collection_name}
            )

        self.tool(
            find_foo,
            name="docs-tidb-find",
            description=self.tool_settings.tool_find_description,
        )
        self.tool(
            list_foo,
            name="docs-tidb-list",
            description=self.tool_settings.tool_list_description,
        )

        if not self.tidb_settings.read_only:
            self.tool(
                store_foo,
                name="docs-tidb-store",
                description=self.tool_settings.tool_store_description,
            )
            self.tool(
                ingest_foo,
                name="docs-tidb-ingest",
                description=self.tool_settings.tool_ingest_description,
            )
