import json
import logging
from typing import Annotated, Any, Optional

from fastmcp import Context, FastMCP
from pydantic import Field

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
from mcp_docs_tidb.tidb import ArbitraryFilter, Entry, Metadata, PyTiDBFilter, TiDBConnector

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
        ) -> str:
            await ctx.debug(f"Storing information in TiDB table {collection_name}")
            entry = Entry(content=information, metadata=metadata)
            self.tidb_connector.store(entry, collection_name=collection_name)
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
            # When a wrap_filters wrapper is in front of this function, it
            # supplies dict_filter directly. Otherwise we may receive the
            # JSON-shaped `query_filter` and translate it here.
            if dict_filter is None and query_filter is not None:
                dict_filter = build_filter_from_arbitrary(query_filter)

            await ctx.debug(
                f"Searching TiDB table {collection_name} with filter={dict_filter!r}"
            )
            entries = self.tidb_connector.search(
                query,
                collection_name=collection_name,
                limit=self.tidb_settings.search_limit,
                dict_filter=dict_filter,
            )
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
        ) -> str:
            await ctx.debug(
                f"Ingesting {len(paths)} input(s) into {collection_name} "
                f"(recursive={recursive}, glob={glob!r}, replace={replace})"
            )
            try:
                files = collect_paths(
                    [Path(p) for p in paths], recursive=recursive, glob=glob
                )
            except FileNotFoundError as exc:
                return f"Error: path not found: {exc}"
            if not files:
                return "No files matched."

            written = ingest_paths(
                files,
                collection_name=collection_name,
                connector=self.tidb_connector,
                chunk_chars=chunk_chars,
                overlap=overlap,
                replace=replace,
            )
            return (
                f"Ingested {written} chunk(s) from {len(files)} file(s) "
                f"into collection {collection_name}."
            )

        find_foo = find
        store_foo = store
        ingest_foo = ingest

        filterable_conditions = (
            self.tidb_settings.filterable_fields_dict_with_conditions()
        )

        if len(filterable_conditions) > 0:
            # Typed per-field arguments replace the generic query_filter.
            find_foo = wrap_filters(find_foo, filterable_conditions)
        elif not self.tidb_settings.allow_arbitrary_filter:
            # No filter UI at all: hide both filter arguments from the schema.
            find_foo = make_partial_function(
                find_foo, {"query_filter": None, "dict_filter": None}
            )
        else:
            # Keep `query_filter`, hide the lower-level pair.
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

        self.tool(
            find_foo,
            name="tidb-find",
            description=self.tool_settings.tool_find_description,
        )

        if not self.tidb_settings.read_only:
            self.tool(
                store_foo,
                name="tidb-store",
                description=self.tool_settings.tool_store_description,
            )
            self.tool(
                ingest_foo,
                name="tidb-ingest",
                description=self.tool_settings.tool_ingest_description,
            )
