# mcp-docs-tidb

An MCP (Model Context Protocol) server that exposes a [TiDB](https://www.pingcap.com/tidb-cloud/) instance as a semantic memory layer. It is heavily inspired by [`mcp-server-qdrant`](https://github.com/qdrant/mcp-server-qdrant) and follows the same `store` / `find` shape, but uses TiDB's native [`VECTOR`](https://docs.pingcap.com/tidb/stable/vector-search-overview) type and `VEC_COSINE_DISTANCE` function as the storage and similarity backend.

This server assumes that a TiDB instance is already running and reachable. It does not provision or start TiDB for you.

The repository can be used in two complementary ways:

- **As an MCP server** — wire it into Claude Desktop / Cursor / Windsurf / Claude Code and call the `tidb-find` / `tidb-store` / `tidb-ingest` tools. See the rest of this README.
- **As a Claude Code skill** — `SKILL.md` in the repo root teaches Claude *how to use this project well* (when to ingest vs. store, dimension pitfalls, search etiquette). See [Use as a Claude Code skill](#use-as-a-claude-code-skill). Skill and MCP server are independent: the skill nudges Claude toward correct usage, the MCP server actually serves the data. They are most useful together.

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager
- A reachable TiDB cluster with vector support (TiDB v8.4+ self-hosted, or TiDB Serverless)
- A user with privileges to `CREATE TABLE`, `INSERT`, and `SELECT` against the target database

## Installation

This project is not published to PyPI. Clone the repository and install dependencies with `uv`:

```bash
git clone https://github.com/your-org/mcp-docs-tidb.git
cd mcp-docs-tidb
uv sync
```

All commands below assume you run them from the repository root, or that you pass `--project /path/to/mcp-docs-tidb` to `uv run`.

## Provided MCP tools

### `tidb-store`

Stores a piece of text (with optional metadata) into a TiDB table.

| Argument | Type | Description |
| --- | --- | --- |
| `information` | string | The text to remember. |
| `collection_name` | string | The TiDB table to store into. Omitted when `COLLECTION_NAME` is configured as default. |
| `metadata` | JSON (optional) | Arbitrary JSON metadata persisted alongside the text. |

The table is auto-created on first write with the following schema:

```sql
CREATE TABLE <collection> (
  id        VARCHAR(36) PRIMARY KEY,
  content   TEXT          NOT NULL,
  metadata  JSON          NULL,
  embedding VECTOR(<dim>) NOT NULL
);
```

This tool is hidden when `TIDB_READ_ONLY=1`.

### `tidb-ingest`

Bulk-ingests local files or directories into a collection. Chunks each file, attaches `metadata.source` / `metadata.chunk`, and (by default) replaces any prior chunks for the same source file. Same engine as the CLI below, exposed to the LLM.

| Argument | Type | Description |
| --- | --- | --- |
| `paths` | list[string] | Files or directories on the server host. |
| `collection_name` | string | Target TiDB table. Omitted when `COLLECTION_NAME` is configured. |
| `recursive` | bool (default `false`) | Recurse into directories. |
| `glob` | string (default `*.md`) | Glob applied to directory entries. |
| `chunk_chars` | int (default `2000`) | Max characters per chunk. |
| `overlap` | int (default `200`) | Chunk overlap in characters. |
| `replace` | bool (default `true`) | Delete existing chunks tagged with the same `source` before inserting. |

> `paths` are resolved on the **server** host, not the MCP client. With stdio transport (the default Claude Desktop setup) they share a filesystem, but remote/Docker deployments may not — see "Loading documents into TiDB" for the equivalent CLI which is generally simpler to operate.

This tool is hidden when `TIDB_READ_ONLY=1`.

### `tidb-find`

Performs a similarity search using `VEC_COSINE_DISTANCE`.

| Argument | Type | Description |
| --- | --- | --- |
| `query` | string | What to search for. |
| `collection_name` | string | The TiDB table to search. Omitted when `COLLECTION_NAME` is configured as default. |

Returns the top `TIDB_SEARCH_LIMIT` (default 10) matches ordered by cosine distance ascending.

## Environment variables

### TiDB connection

| Variable | Default | Description |
| --- | --- | --- |
| `TIDB_HOST` | `127.0.0.1` | TiDB host. |
| `TIDB_PORT` | `4000` | TiDB port. |
| `TIDB_USER` | `root` | TiDB user. |
| `TIDB_PASSWORD` | _(empty)_ | TiDB password. |
| `TIDB_DATABASE` | `test` | Database/schema name. |
| `TIDB_SSL_VERIFY_CERT` | `0` | Set to `1` to enable TLS (required for TiDB Serverless). |
| `TIDB_SSL_CA` | _(unset)_ | Optional CA bundle path, e.g. `/etc/ssl/cert.pem`. |

### Behavior

| Variable | Default | Description |
| --- | --- | --- |
| `COLLECTION_NAME` | _(unset)_ | Default table. When set, the MCP tools drop their `collection_name` argument. |
| `TIDB_SEARCH_LIMIT` | `10` | Max rows returned from `tidb-find`. |
| `TIDB_READ_ONLY` | `0` | When `1`, the `tidb-store` tool is not registered. |
| `TIDB_USE_VECTOR_INDEX` | `0` | When `1`, auto-created tables include an inline `VECTOR INDEX ... USING HNSW` on the embedding column. Requires TiDB v8.4+ and a TiFlash replica in the cluster — see [Vector index](#vector-index). |
| `EMBEDDING_PROVIDER` | `fastembed` | Embedding provider (only `fastembed` is supported today). |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | FastEmbed model name. |
| `TOOL_STORE_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `tidb-store`. |
| `TOOL_FIND_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `tidb-find`. |
| `TOOL_INGEST_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `tidb-ingest`. |
| `TIDB_ALLOW_ARBITRARY_FILTER` | `0` | When `1`, exposes a `query_filter` argument on `tidb-find` that accepts a JSON filter spec. |

## Quick start

### 1. Run TiDB locally

The easiest path on macOS / Linux is `tiup playground`:

```bash
tiup playground v8.5 --tiflash 0 --db 1 --pd 1 --kv 1
```

The default endpoint is `127.0.0.1:4000`, user `root`, no password — which matches the defaults of this server.

> Vector search requires TiDB v8.4 or newer. Older versions will fail at `CREATE TABLE` because the `VECTOR` type is unknown.

### 2. Run the MCP server

```bash
uv run mcp-docs-tidb
```

To accept HTTP clients instead of stdio:

```bash
uv run mcp-docs-tidb --transport streamable-http
# or, legacy SSE
uv run mcp-docs-tidb --transport sse
```

### 3. Wire it up to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp-docs-tidb", "mcp-docs-tidb"],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "mcp_memory"
      }
    }
  }
}
```

Replace `/path/to/mcp-docs-tidb` with the absolute path to your local clone.

### 4. Wire it up to Claude Code

**Option A — CLI (one-shot)**

```bash
claude mcp add mcp-docs-tidb uv \
  --args "run,--project,/path/to/mcp-docs-tidb,mcp-docs-tidb" \
  -e TIDB_HOST=127.0.0.1 \
  -e TIDB_PORT=4000 \
  -e TIDB_USER=root \
  -e TIDB_DATABASE=test \
  -e COLLECTION_NAME=mcp_memory
```

This appends the server to your user-global `~/.claude/settings.json`. Append `--scope project` to write to `.claude/settings.json` instead (project-local).

**Option B — edit `settings.json` directly**

User-global (`~/.claude/settings.json`) or project-local (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/mcp-docs-tidb", "mcp-docs-tidb"],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "mcp_memory"
      }
    }
  }
}
```

Replace `/path/to/mcp-docs-tidb` with the absolute path to your local clone.

Restart Claude Code (or run `/mcp` to reload without restarting) after editing. Confirm the server is live with `/mcp` — `mcp-docs-tidb` should appear in the list with its three tools.

### 5. Optional: add a vector index

See [Vector index](#vector-index) below. The short version: set `TIDB_USE_VECTOR_INDEX=1` before the first ingest, or run an `ALTER TABLE` later. Either way you need a TiFlash node in the cluster.

## Vector index

`tidb-find` issues `SELECT ... ORDER BY VEC_COSINE_DISTANCE(embedding, ?) LIMIT N`. Without an index, that's a full table scan — fine up to ~10⁴ rows, slow beyond. TiDB supports an [HNSW vector index](https://docs.pingcap.com/ai/vector-search-index/) that turns this into an approximate-nearest-neighbour lookup.

### Option A. `TIDB_USE_VECTOR_INDEX=1` — auto-create with the table

Set the variable *before* the first row is inserted. The server then issues:

```sql
CREATE TABLE <collection> (
  id        VARCHAR(36) PRIMARY KEY,
  content   TEXT          NOT NULL,
  metadata  JSON          NULL,
  embedding VECTOR(<dim>) NOT NULL,
  VECTOR INDEX `idx_embedding`
    ((VEC_COSINE_DISTANCE(`embedding`))) USING HNSW
);
```

If the table already exists, this flag has no effect — TiDB only adds the index on creation. Drop the table or use Option B.

### Option B. `ALTER TABLE` an existing table

```sql
ALTER TABLE mcp_memory
  ADD VECTOR INDEX idx_embedding
    ((VEC_COSINE_DISTANCE(embedding))) USING HNSW;
```

### Requirements & caveats

- **TiDB v8.4+** (v8.5+ recommended). Older versions don't support the `VECTOR INDEX` syntax.
- **A TiFlash replica is required.** TiDB allocates one automatically at index creation, but the cluster must actually have a TiFlash node — otherwise the `CREATE TABLE` or `ALTER TABLE` fails. `tiup playground` includes TiFlash by default; some self-hosted deployments and minimal Docker setups don't. Check with `SELECT type FROM information_schema.cluster_info WHERE type='tiflash'`.
- The index uses **cosine distance** (`VEC_COSINE_DISTANCE`). If you need `VEC_L2_DISTANCE` instead, do not use this flag — drop the auto index and create your own via `ALTER TABLE`.
- The index is **read-side only**: writes still go through TiKV; reads use TiFlash. Expect a brief lag before fresh inserts are searchable through the index.

Without an index, `tidb-find` still works — it just performs a full scan.

## Embedding dimension

The embedding column is declared as `VECTOR(<dim>)`, where `<dim>` is whatever the configured embedding provider reports from `get_vector_size()`. Common values:

| `EMBEDDING_MODEL` | `<dim>` |
| --- | --- |
| `sentence-transformers/all-MiniLM-L6-v2` (default) | `384` |
| `BAAI/bge-small-en-v1.5` | `384` |
| `BAAI/bge-base-en-v1.5` | `768` |
| `BAAI/bge-large-en-v1.5` | `1024` |

The dimension is baked into the table at first write (`CREATE TABLE ... VECTOR(<dim>)`) and cannot be changed afterwards — TiDB enforces it at the type level. **If you switch to an embedding model with a different dimension, you must either:**

1. point `COLLECTION_NAME` at a fresh table, or
2. `DROP TABLE <collection>` and let the server recreate it on the next write.

Attempting to write a different-sized vector into an existing table will fail with a `VECTOR` dimension mismatch error from TiDB.

## Use as a Claude Code skill

[Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills) are small Markdown documents loaded into Claude's context on demand, giving it project-specific guidance. This repository ships one at [`SKILL.md`](./SKILL.md) (`name: tidb-docs`) covering when to use which MCP tool, dimension/index pitfalls, filter semantics, and recovery procedures.

The skill needs to live under a `skills/<name>/SKILL.md` directory that Claude Code scans. Pick one location:

### Project-scoped (recommended)

Only active when Claude Code runs inside this checkout. Edits to `SKILL.md` are tracked in git.

```bash
mkdir -p .claude/skills/tidb-docs
ln -s "$(pwd)/SKILL.md" .claude/skills/tidb-docs/SKILL.md
```

### User-global

Active in every project you open with Claude Code.

```bash
mkdir -p ~/.claude/skills/tidb-docs
ln -s "$(pwd)/SKILL.md" ~/.claude/skills/tidb-docs/SKILL.md
```

(If your environment does not support symlinks, `cp` instead — just remember to re-copy after edits.)

Either way, restart Claude Code (or run `/skills` if your build supports it) so the new skill is picked up. Once loaded, asking Claude to "ingest these docs into TiDB" or "search the TiDB knowledge base" should trigger it.

The skill is **independent of how this project is installed**: you can pull `SKILL.md` into a project that connects to a remote `mcp-docs-tidb`, or use it locally with a `tiup playground` instance — the guidance is the same.

## Loading documents into TiDB

There are two routes to populate a collection from existing files:

1. **MCP tool `tidb-ingest`** — ask the LLM to "load `~/docs` into the `kb` collection". The server reads the files itself and writes the chunks. Good for interactive use from Claude Desktop / Cursor / Windsurf when the MCP server and client share a filesystem.
2. **`mcp-docs-tidb-ingest` CLI** — run outside of any LLM conversation, e.g. from cron or CI to refresh a corpus. Same code path as the MCP tool; choose whichever fits your workflow.

### CLI

```bash
# Ingest every Markdown file under ./docs into the `kb` table.
TIDB_HOST=127.0.0.1 TIDB_PORT=4000 TIDB_USER=root TIDB_DATABASE=test \
  uv run mcp-docs-tidb-ingest \
    --collection kb \
    --recursive --glob '*.md' \
    ./docs

# Re-run after editing — same files get replaced atomically per file.
uv run mcp-docs-tidb-ingest --collection kb --recursive --glob '*.md' ./docs
```

Flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--collection` | _(required)_ | Target TiDB table (auto-created on first write). |
| `--chunk-chars` | `2000` | Characters per chunk. |
| `--overlap` | `200` | Overlap between adjacent chunks, in characters. |
| `-r`, `--recursive` | off | Recurse into directories. |
| `--glob` | `*.md` | Glob applied to directory inputs. |
| `--no-replace` | off | Append instead of replacing previously-ingested chunks for the same source file. |
| `-v`, `--verbose` | off | Log per-file progress. |

### What gets written

For each input file, the CLI:

1. Reads the file as UTF-8.
2. Splits it into character-based chunks of `--chunk-chars` with `--overlap` overlap.
3. (By default) deletes existing rows whose `metadata.source` equals the absolute path of this file.
4. Inserts one row per chunk with `metadata = {"source": "<abs path>", "chunk": <0-based index>}`.

So a re-ingest of the same file produces the same row count regardless of how many times you've run it — useful for cron-driven refreshes.

### Re-ingest semantics

- **Default (replace per source)**: only the affected file's chunks are removed. Other files in the same collection are untouched.
- **`--no-replace`**: previously-ingested chunks stay in place; new chunks are added. Use this only if you actually want versioned history.
- **Schema change** (e.g. switching embedding models with a different dim): the CLI cannot recover from this — `DROP TABLE <collection>` first, then re-ingest.

### Python API

`mcp-docs-tidb-ingest` is a thin wrapper around `mcp_docs_tidb.ingest.ingest_paths`, which you can call directly if you need to embed ingestion into your own pipeline:

```python
import asyncio
from pathlib import Path

from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.ingest import ingest_paths, collect_paths
from mcp_docs_tidb.settings import EmbeddingProviderSettings, TiDBSettings
from mcp_docs_tidb.tidb import TiDBConnector

async def main():
    connector = TiDBConnector(
        settings=TiDBSettings(),
        embedding_provider=create_embedding_provider(EmbeddingProviderSettings()),
    )
    try:
        files = collect_paths([Path("docs")], recursive=True, glob="*.md")
        n = await ingest_paths(
            files,
            collection_name="kb",
            connector=connector,
            chunk_chars=1500,
            overlap=150,
            extra_metadata={"team": "platform"},
        )
        print(f"wrote {n} chunks")
    finally:
        await connector.close()

asyncio.run(main())
```

`extra_metadata` is merged into every chunk's metadata, alongside the standard `source` / `chunk` keys. Combined with [filterable fields](#filtering-search-results) you can then filter `tidb-find` by, e.g., `team`.

## Filtering search results

`tidb-find` supports filtering on values inside the `metadata` JSON column. Two mechanisms are available — pick at most one per deployment.

### Option A. Declared filterable fields (recommended)

Define the metadata keys you want to filter on, including their type and the operator exposed to the LLM. Each declared field is materialised as a `VIRTUAL` generated column on the auto-created table and indexed for fast lookups.

The server reads this list from a `TiDBSettings(filterable_fields=...)` constructor argument, so you would build a small wrapper module:

```python
# my_tidb_server.py
from mcp_docs_tidb.mcp_server import TiDBMCPServer
from mcp_docs_tidb.settings import (
    EmbeddingProviderSettings,
    FilterableField,
    TiDBSettings,
    ToolSettings,
)

tidb_settings = TiDBSettings(
    filterable_fields=[
        FilterableField(
            name="category",
            description="Memory category (e.g. 'work', 'personal')",
            field_type="keyword",
            condition="==",
        ),
        FilterableField(
            name="year",
            description="Year the memory refers to",
            field_type="integer",
            condition=">=",
        ),
        FilterableField(
            name="tags",
            description="Match any of these tags",
            field_type="keyword",
            condition="any",
        ),
    ],
)

mcp = TiDBMCPServer(
    tool_settings=ToolSettings(),
    tidb_settings=tidb_settings,
    embedding_provider_settings=EmbeddingProviderSettings(),
)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

`tidb-find` is then exposed to the LLM with the typed arguments `category: str | None`, `year: int | None`, `tags: list[str] | None`.

Supported `field_type` × `condition` combinations:

| `field_type` | Allowed `condition` |
| --- | --- |
| `keyword` | `==`, `!=`, `any`, `except` |
| `integer` | `==`, `!=`, `>`, `>=`, `<`, `<=`, `any`, `except` |
| `float` | `>`, `>=`, `<`, `<=` |
| `boolean` | `==`, `!=` |

If `condition` is omitted, the field is still indexed but no argument is exposed to the LLM.

### Option B. Arbitrary JSON filter

Set `TIDB_ALLOW_ARBITRARY_FILTER=1` to expose a `query_filter` argument on `tidb-find`. The value is a JSON object:

```json
{
  "must":     [{"field": "category", "op": "==", "value": "work"}],
  "must_not": [{"field": "archived", "op": "==", "value": "true"}]
}
```

Supported `op` values: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`.

This mode does not require declaring fields up-front (and does not create indexes), so it is useful for ad-hoc exploration but slower on large tables.

## Connecting to TiDB Serverless

TiDB Serverless requires TLS. Point `TIDB_SSL_CA` at your system CA bundle:

```bash
TIDB_HOST=gateway01.us-west-2.prod.aws.tidbcloud.com \
TIDB_PORT=4000 \
TIDB_USER='xxxxx.root' \
TIDB_PASSWORD='your-password' \
TIDB_DATABASE='test' \
TIDB_SSL_VERIFY_CERT=1 \
TIDB_SSL_CA=/etc/ssl/cert.pem \
COLLECTION_NAME=mcp_memory \
uv run mcp-docs-tidb
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src
uv run mypy src
```

## License

Apache License 2.0.
