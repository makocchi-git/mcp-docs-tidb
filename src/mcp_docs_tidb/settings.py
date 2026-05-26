from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mcp_docs_tidb.embeddings.types import EmbeddingProviderType

DEFAULT_TOOL_STORE_DESCRIPTION = (
    "Keep the memory for later use, when you are asked to remember something."
)
DEFAULT_TOOL_FIND_DESCRIPTION = (
    "Look up memories in TiDB. Use this tool when you need to: \n"
    " - Find memories by their content \n"
    " - Access memories for further analysis \n"
    " - Get some personal information about the user"
)
DEFAULT_TOOL_INGEST_DESCRIPTION = (
    "Bulk-ingest local files or directories into a TiDB collection. \n"
    "Use this when the user asks to load, index, or refresh a set of "
    "documents into the vector store, rather than remembering a single "
    "free-form note (use `docs-tidb-store` for that). \n"
    "Files are split into chunks; re-running on the same file replaces "
    "its previous chunks by default."
)
DEFAULT_TOOL_LIST_DESCRIPTION = (
    "List the documents currently registered in a TiDB collection. \n"
    "Returns one entry per distinct `metadata.source` with its chunk count "
    "and latest `mtime` / `ingested_at` (Unix epoch seconds). \n"
    "Use this to inspect what has already been ingested, e.g. before "
    "re-ingesting, or to check freshness."
)

METADATA_COLUMN = "metadata"
CONTENT_COLUMN = "content"
EMBEDDING_COLUMN = "embedding"
ID_COLUMN = "id"


class ToolSettings(BaseSettings):
    """
    Configuration for all the tools.
    """

    model_config = SettingsConfigDict(
        populate_by_name=True,
        case_sensitive=True,
        env_ignore_empty=True,
    )

    tool_store_description: str = Field(
        default=DEFAULT_TOOL_STORE_DESCRIPTION,
        validation_alias="TOOL_STORE_DESCRIPTION",
    )
    tool_find_description: str = Field(
        default=DEFAULT_TOOL_FIND_DESCRIPTION,
        validation_alias="TOOL_FIND_DESCRIPTION",
    )
    tool_ingest_description: str = Field(
        default=DEFAULT_TOOL_INGEST_DESCRIPTION,
        validation_alias="TOOL_INGEST_DESCRIPTION",
    )
    tool_list_description: str = Field(
        default=DEFAULT_TOOL_LIST_DESCRIPTION,
        validation_alias="TOOL_LIST_DESCRIPTION",
    )
    ingest_max_paths: int = Field(
        default=1000,
        ge=1,
        le=100_000,
        validation_alias="TIDB_INGEST_MAX_PATHS",
    )
    ingest_root: str | None = Field(
        default=None,
        validation_alias="TIDB_INGEST_ROOT",
    )


class EmbeddingProviderSettings(BaseSettings):
    """
    Configuration for the embedding provider.
    """

    model_config = SettingsConfigDict(
        populate_by_name=True,
        case_sensitive=True,
        env_ignore_empty=True,
    )

    provider_type: EmbeddingProviderType = Field(
        default=EmbeddingProviderType.FASTEMBED,
        validation_alias="EMBEDDING_PROVIDER",
    )
    model_name: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        validation_alias="EMBEDDING_MODEL",
    )


class FilterableField(BaseModel):
    """
    Declaration of a metadata field that can be used to filter `tidb-find`
    queries. The field is stored inside the `metadata` JSON column; when
    declared here a generated, virtual SQL column is added so it can be
    indexed.
    """

    name: str = Field(description="The metadata key to filter on (top-level only).")
    description: str = Field(
        description="Description shown in the MCP tool argument's schema."
    )
    field_type: Literal["keyword", "integer", "float", "boolean"] = Field(
        description="The logical type of the field. Determines the SQL coercion used."
    )
    condition: (
        Literal["==", "!=", ">", ">=", "<", "<=", "any", "except"] | None
    ) = Field(
        default=None,
        description=(
            "Operator exposed as an MCP tool argument. If omitted, the field is "
            "indexed but no argument is exposed."
        ),
    )
    required: bool = Field(
        default=False,
        description="Whether the argument is required when calling the tool.",
    )


class TiDBSettings(BaseSettings):
    """
    Configuration for the TiDB connector.

    TiDB is MySQL-protocol compatible, so connection parameters mirror MySQL's.
    The user is expected to point this at an already running TiDB instance
    (local tiup playground, self-hosted, or TiDB Serverless).

    ``case_sensitive=True`` keeps field-name-based env lookups from picking
    up unrelated OS variables such as ``USER`` or ``HOST`` (which would
    otherwise leak into the ``user`` / ``host`` fields and silently
    override the documented defaults). ``env_ignore_empty=True`` makes
    empty values like ``TIDB_USER=`` behave the same as leaving the
    variable unset, so the documented defaults still apply.
    """

    model_config = SettingsConfigDict(
        populate_by_name=True,
        case_sensitive=True,
        env_ignore_empty=True,
    )

    host: str = Field(default="127.0.0.1", validation_alias="TIDB_HOST")
    port: int = Field(default=4000, validation_alias="TIDB_PORT")
    user: str = Field(default="root", validation_alias="TIDB_USER")
    password: str = Field(default="", validation_alias="TIDB_PASSWORD")
    database: str = Field(default="test", validation_alias="TIDB_DATABASE")

    ssl_verify_cert: bool = Field(
        default=False, validation_alias="TIDB_SSL_VERIFY_CERT"
    )
    ssl_ca: str | None = Field(default=None, validation_alias="TIDB_SSL_CA")

    collection_name: str | None = Field(
        default=None, validation_alias="COLLECTION_NAME"
    )
    search_limit: int = Field(default=10, ge=1, le=10_000, validation_alias="TIDB_SEARCH_LIMIT")
    read_only: bool = Field(default=False, validation_alias="TIDB_READ_ONLY")
    connect_timeout: float = Field(
        default=10.0,
        ge=0.1,
        le=300.0,
        validation_alias="TIDB_CONNECT_TIMEOUT",
    )
    read_timeout: float = Field(
        default=30.0,
        ge=0.1,
        le=600.0,
        validation_alias="TIDB_READ_TIMEOUT",
    )

    use_vector_index: bool = Field(
        default=True, validation_alias="TIDB_USE_VECTOR_INDEX"
    )

    filterable_fields: list[FilterableField] | None = Field(default=None)

    allow_arbitrary_filter: bool = Field(
        default=False, validation_alias="TIDB_ALLOW_ARBITRARY_FILTER"
    )

    def filterable_fields_dict(self) -> dict[str, FilterableField]:
        if self.filterable_fields is None:
            return {}
        return {field.name: field for field in self.filterable_fields}

    def filterable_fields_dict_with_conditions(self) -> dict[str, FilterableField]:
        if self.filterable_fields is None:
            return {}
        return {
            field.name: field
            for field in self.filterable_fields
            if field.condition is not None
        }

    @model_validator(mode="after")
    def _validate_port(self) -> "TiDBSettings":
        if not (0 < self.port < 65536):
            raise ValueError(f"TIDB_PORT must be in 1..65535, got {self.port}")
        return self
