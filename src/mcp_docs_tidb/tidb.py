"""
TiDB-backed storage layer for mcp-docs-tidb.

Built on top of pytidb's ORM-style API: each "collection" maps to a TiDB
table whose schema is generated on demand from the embedding provider's
dimension. Rows are inserted, searched, and deleted through pytidb's
synchronous ``Table`` API.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from pydantic import BaseModel
from pytidb import TiDBClient
from pytidb.orm.types import JSON, TEXT
from pytidb.schema import Column, Field, TableModel

from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.settings import (
    CONTENT_COLUMN,
    EMBEDDING_COLUMN,
    ID_COLUMN,
    METADATA_COLUMN,
    FilterableField,
    TiDBSettings,
)

logger = logging.getLogger(__name__)

Metadata = dict[str, Any]
ArbitraryFilter = dict[str, Any]
PyTiDBFilter = dict[str, Any]

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Entry(BaseModel):
    """A single entry stored in TiDB."""

    content: str
    metadata: Metadata | None = None


def _validate_identifier(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(
            f"Invalid TiDB table identifier: {name!r}. "
            "Allowed characters are [A-Za-z0-9_] and the name must not start with a digit."
        )
    return name


def format_db_error(exc: BaseException, settings: TiDBSettings) -> str:
    """
    Build a one-paragraph human-friendly error message for a TiDB-side
    failure. Includes the connection target (host/port/user/database) so
    the operator can immediately spot misconfiguration, plus the underlying
    driver error string. Intended for CLI stderr and MCP tool replies —
    use this instead of letting a raw SQLAlchemy / pymysql traceback leak
    out.
    """
    # SQLAlchemy wraps the DBAPI error under `.orig`; surface that when
    # available because it carries the actual MySQL/TiDB message.
    underlying = getattr(exc, "orig", None) or exc
    return (
        f"Error: failed to access TiDB at {settings.host}:{settings.port} "
        f"(user={settings.user!r}, database={settings.database!r}): "
        f"{underlying}. "
        "Hint: verify TIDB_HOST, TIDB_PORT, TIDB_USER, TIDB_PASSWORD, "
        "TIDB_DATABASE and that the TiDB instance is reachable."
    )


def _build_chunk_model(
    table_name: str,
    embedding_provider: EmbeddingProvider,
    use_vector_index: bool,
) -> type[TableModel]:
    """
    Dynamically construct a ``TableModel`` subclass for ``table_name``.

    Each collection has its own model class because pytidb's ``TableModel``
    binds ``__tablename__`` (and the underlying table object) to the class.
    The class is generated through ``TableModel.__class__`` so the
    metaclass runs and registers the table.
    """
    embedding_field = embedding_provider.VectorField(
        source_field=CONTENT_COLUMN,
        index=use_vector_index,
    )
    annotations = {
        ID_COLUMN: str,
        CONTENT_COLUMN: str,
        f"{METADATA_COLUMN}_": dict,
        EMBEDDING_COLUMN: list[float],
    }
    namespace: dict[str, Any] = {
        "__tablename__": table_name,
        "__annotations__": annotations,
        ID_COLUMN: Field(
            primary_key=True,
            max_length=36,
            default_factory=lambda: uuid.uuid4().hex,
        ),
        CONTENT_COLUMN: Field(
            sa_column=Column(CONTENT_COLUMN, TEXT, nullable=False),
        ),
        f"{METADATA_COLUMN}_": Field(
            default=None,
            sa_column=Column(METADATA_COLUMN, JSON, nullable=True),
        ),
        EMBEDDING_COLUMN: embedding_field,
    }
    class_name = f"Chunk_{table_name}"
    return TableModel.__class__(  # type: ignore[call-overload, no-any-return]
        class_name, (TableModel,), namespace
    )


class TiDBConnector:
    """
    Connection helper for a TiDB instance. Each "collection" maps to a TiDB
    table whose schema is generated lazily on first use.
    """

    def __init__(
        self,
        settings: TiDBSettings,
        embedding_provider: EmbeddingProvider,
        filterable_fields: dict[str, FilterableField] | None = None,
    ):
        self._settings = settings
        self._embedding_provider = embedding_provider
        self._default_collection_name = settings.collection_name
        self._filterable_fields = filterable_fields or {}
        self._use_vector_index = settings.use_vector_index
        self._client: TiDBClient | None = None
        self._tables: dict[str, Any] = {}

    def _get_client(self) -> TiDBClient:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self._settings.ssl_verify_cert:
                kwargs["enable_ssl"] = True
            if self._settings.ssl_ca:
                kwargs["ssl_ca"] = self._settings.ssl_ca
            self._client = TiDBClient.connect(
                host=self._settings.host,
                port=self._settings.port,
                username=self._settings.user,
                password=self._settings.password,
                database=self._settings.database,
                ensure_db=True,
                **kwargs,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None
            self._tables.clear()

    def _resolve_collection(self, collection_name: str | None) -> str:
        name = collection_name or self._default_collection_name
        if not name:
            raise ValueError(
                "No collection name provided and COLLECTION_NAME is not configured."
            )
        return _validate_identifier(name)

    def _get_table(self, table_name: str):
        """Open or create the pytidb ``Table`` for ``table_name``."""
        if table_name in self._tables:
            return self._tables[table_name]

        client = self._get_client()
        model = _build_chunk_model(
            table_name,
            self._embedding_provider,
            self._use_vector_index,
        )
        table = client.create_table(schema=model, if_exists="skip")
        self._tables[table_name] = table
        return table

    def _collection_exists(self, table_name: str) -> bool:
        if table_name in self._tables:
            return True
        client = self._get_client()
        return client.has_table(table_name)

    def store(self, entry: Entry, *, collection_name: str | None = None) -> None:
        table = self._get_table(self._resolve_collection(collection_name))
        table.insert(
            {
                CONTENT_COLUMN: entry.content,
                f"{METADATA_COLUMN}_": entry.metadata,
            }
        )

    def search(
        self,
        query: str,
        *,
        collection_name: str | None = None,
        limit: int = 10,
        dict_filter: PyTiDBFilter | None = None,
    ) -> list[Entry]:
        name = self._resolve_collection(collection_name)
        if not self._collection_exists(name):
            return []

        table = self._get_table(name)
        search = table.search(query, search_type="vector")
        if dict_filter is not None:
            search = search.filter(dict_filter)
        rows = search.limit(int(limit)).to_list()

        # pytidb's row dicts use the attribute name, which is `metadata_`
        # here because plain `metadata` collides with the declarative
        # base's reserved attribute.
        entries: list[Entry] = []
        for row in rows:
            raw_meta = row.get(f"{METADATA_COLUMN}_")
            metadata: Metadata | None = raw_meta if isinstance(raw_meta, dict) else None
            entries.append(Entry(content=row[CONTENT_COLUMN], metadata=metadata))
        return entries

    def delete_by_metadata_field(
        self,
        *,
        collection_name: str | None,
        field_name: str,
        field_value: Any,
    ) -> int:
        """
        Delete every row in ``collection_name`` whose top-level metadata
        field ``field_name`` equals ``field_value``. Returns the number of
        rows deleted.
        """
        if not _IDENT_RE.match(field_name):
            raise ValueError(f"Invalid metadata field name: {field_name!r}")
        name = self._resolve_collection(collection_name)
        if not self._collection_exists(name):
            return 0

        # Use raw execute() so we can report the affected rowcount; the
        # high-level Table.delete() API discards it.
        client = self._get_client()
        result = client.execute(
            f"DELETE FROM `{name}` "
            f"WHERE JSON_UNQUOTE(JSON_EXTRACT(`{METADATA_COLUMN}`, "
            f"'$.{field_name}')) = :value",
            {"value": field_value},
        )
        return int(result.rowcount)

    def truncate_collection(self, *, collection_name: str | None) -> bool:
        """
        Remove every row from ``collection_name`` via ``TRUNCATE TABLE``.
        Returns ``True`` when a truncate was issued and ``False`` when the
        table does not exist yet (no-op).

        The table schema is preserved; only its contents are emptied. The
        cached pytidb ``Table`` is kept — re-popping it would force pytidb
        to re-register the dynamically-built model class against
        SQLAlchemy's metadata, which collides with the prior registration.
        """
        name = self._resolve_collection(collection_name)
        if not self._collection_exists(name):
            return False
        client = self._get_client()
        client.execute(f"TRUNCATE TABLE `{name}`")
        return True

    def get_max_numeric_metadata_value(
        self,
        *,
        collection_name: str | None,
        match_field: str,
        match_value: Any,
        value_field: str,
    ) -> float | None:
        """
        Return ``MAX(metadata.<value_field>)`` (cast to DOUBLE) across rows
        whose ``metadata.<match_field>`` equals ``match_value``. Returns
        ``None`` when no matching row exists or the table is absent.

        Used by the incremental-ingest path to look up the previously
        recorded ``mtime`` for a given source file.
        """
        for field in (match_field, value_field):
            if not _IDENT_RE.match(field):
                raise ValueError(f"Invalid metadata field name: {field!r}")
        name = self._resolve_collection(collection_name)
        if not self._collection_exists(name):
            return None

        client = self._get_client()
        rows = client.query(
            f"SELECT MAX(CAST(JSON_EXTRACT(`{METADATA_COLUMN}`, "
            f"'$.{value_field}') AS DOUBLE)) AS v FROM `{name}` "
            f"WHERE JSON_UNQUOTE(JSON_EXTRACT(`{METADATA_COLUMN}`, "
            f"'$.{match_field}')) = :value",
            {"value": match_value},
        ).to_list()
        if not rows:
            return None
        v = rows[0].get("v")
        return float(v) if v is not None else None
