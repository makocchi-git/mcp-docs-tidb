from mcp_docs_tidb.mcp_server import TiDBMCPServer
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    TiDBSettings,
    ToolSettings,
)

mcp = TiDBMCPServer(
    tool_settings=ToolSettings(),
    tidb_settings=TiDBSettings(),
    embedding_provider_settings=EmbeddingProviderSettings(),
)
