"""
Test fixtures shared by unit and integration tests.

Integration tests assume a TiDB instance is reachable at the address
configured below (defaults match `tiup playground` on localhost). They are
skipped automatically when the port is unreachable.
"""

from __future__ import annotations

import hashlib
import math
import os
import socket
import uuid
from typing import Any, Iterator

import pytest
from pydantic import PrivateAttr

from mcp_docs_tidb.embeddings.base import EmbeddingProvider
from mcp_docs_tidb.settings import TiDBSettings
from mcp_docs_tidb.tidb import TiDBConnector

TIDB_HOST = os.environ.get("TIDB_HOST", "127.0.0.1")
TIDB_PORT = int(os.environ.get("TIDB_PORT", "4000"))
TIDB_USER = os.environ.get("TIDB_USER", "root")
TIDB_PASSWORD = os.environ.get("TIDB_PASSWORD", "")
TIDB_DATABASE = os.environ.get("TIDB_DATABASE", "test")


def _tidb_reachable() -> bool:
    try:
        with socket.create_connection((TIDB_HOST, TIDB_PORT), timeout=1.0):
            return True
    except OSError:
        return False


requires_tidb = pytest.mark.skipif(
    not _tidb_reachable(),
    reason=f"TiDB not reachable at {TIDB_HOST}:{TIDB_PORT}",
)


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """
    A tiny, dependency-free embedding provider used in tests.

    It hashes the input string into a fixed-size float vector and L2-normalises
    it, so `VEC_COSINE_DISTANCE` returns stable, predictable values without
    requiring the multi-hundred-megabyte FastEmbed download.
    """

    _dim: int = PrivateAttr()

    def __init__(self, dim: int = 8, **data: Any):
        super().__init__(
            provider="deterministic",
            model_name="sha256",
            dimensions=dim,
            **data,
        )
        self._dim = dim

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        reps = (self._dim + len(digest) - 1) // len(digest)
        buf = (digest * reps)[: self._dim]
        vec = [(b / 127.5) - 1.0 for b in buf]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def get_source_embedding(
        self, source: Any, source_type: str = "text", **kwargs: Any
    ) -> list[float]:
        return self._embed_one(source)

    def get_query_embedding(
        self, query: Any, source_type: str = "text", **kwargs: Any
    ) -> list[float]:
        return self._embed_one(query)


@pytest.fixture
def embedding_provider() -> DeterministicEmbeddingProvider:
    return DeterministicEmbeddingProvider(dim=8)


@pytest.fixture
def tidb_settings() -> TiDBSettings:
    return TiDBSettings(
        host=TIDB_HOST,
        port=TIDB_PORT,
        user=TIDB_USER,
        password=TIDB_PASSWORD,
        database=TIDB_DATABASE,
    )


@pytest.fixture
def connector(
    tidb_settings: TiDBSettings,
    embedding_provider: DeterministicEmbeddingProvider,
) -> Iterator[TiDBConnector]:
    conn = TiDBConnector(
        settings=tidb_settings,
        embedding_provider=embedding_provider,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def collection_name() -> str:
    """A fresh, unique TiDB table name for each test."""
    return f"mcp_test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def cleanup_table(tidb_settings: TiDBSettings) -> Iterator[list[str]]:
    """
    Drops any tables that tests asked to be cleaned up. Tests register the
    names they want dropped on the yielded list.
    """
    tables: list[str] = []
    yield tables

    if not _tidb_reachable():
        return

    from pytidb import TiDBClient

    client = TiDBClient.connect(
        host=tidb_settings.host,
        port=tidb_settings.port,
        username=tidb_settings.user,
        password=tidb_settings.password,
        database=tidb_settings.database,
    )
    try:
        for table in tables:
            client.drop_table(table, if_not_exists="skip")
    finally:
        client.disconnect()
