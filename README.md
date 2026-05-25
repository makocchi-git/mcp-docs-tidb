<h1 align="center">
  MCP server for storing/retrieving documents in TiDB
</h1>

<p align="center">
  <strong>🇺🇸English</strong> ·
  <a href="docs/README.ja.md">🇯🇵日本語</a>
</p>

# mcp-docs-tidb

An MCP (Model Context Protocol) server that exposes a [TiDB](https://www.pingcap.com/tidb-cloud/) instance as a semantic memory layer, using TiDB's native [`VECTOR`](https://docs.pingcap.com/tidb/stable/vector-search-overview) type and `VEC_COSINE_DISTANCE` function as the storage and similarity backend.

This server assumes that a TiDB instance is already running and reachable. It does not provision or start TiDB for you.

The repository can be used in two complementary ways:

- **As an MCP server** — wire it into Claude Desktop / Cursor / Windsurf / Claude Code and call the `docs-tidb-find` / `docs-tidb-list` / `docs-tidb-store` / `docs-tidb-ingest` tools. See the rest of this README.
- **As a Claude Code skill** — `SKILL.md` in the repo root teaches Claude *how to use this project well* (when to ingest vs. store, dimension pitfalls, search etiquette). See [Use as a Claude Code skill](#use-as-a-claude-code-skill). Skill and MCP server are independent: the skill nudges Claude toward correct usage, the MCP server actually serves the data. They are most useful together.

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager
- A reachable TiDB cluster with vector support (TiDB v8.4+ self-hosted, or TiDB Cloud Starter)
- A user with privileges to `CREATE TABLE`, `INSERT`, and `SELECT` against the target database; `CREATE DATABASE` is also required if `TIDB_DATABASE` does not exist yet (the server creates it automatically on first connect)

## Installation

`uvx` (bundled with [uv](https://docs.astral.sh/uv/)) can run the tools directly from GitHub — no clone required:

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

An isolated environment is created on first run and cached for subsequent calls. To pin to a specific commit or tag, append `@<ref>` to the URL (e.g. `@main`, `@v0.1.0`).

For development (running tests, linting, etc.), clone the repository and use `uv run` instead:

```bash
git clone https://github.com/makocchi-git/mcp-docs-tidb.git
cd mcp-docs-tidb
uv sync
```

## Provided MCP tools

### `docs-tidb-store`

Stores a piece of text (with optional metadata) into a TiDB table.

| Argument | Type | Description |
| --- | --- | --- |
| `information` | string | The text to remember. |
| `collection_name` | string | The TiDB table to store into. Omitted when `COLLECTION_NAME` is configured as default. |
| `metadata` | JSON (optional) | Arbitrary JSON metadata persisted alongside the text. |
| `mtime` | float (optional) | Source modification time as a Unix epoch. Stored under `metadata.mtime`; takes precedence over any `mtime` already in `metadata`. Use it when the caller knows the freshness of the text (e.g. file mtime, upstream `Last-Modified`) so it can later be filtered or skipped on re-ingest. |

Regardless of input, the server always stamps `metadata.ingested_at` with the current Unix epoch.

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

### `docs-tidb-ingest`

Bulk-ingests local files or directories into a collection. Chunks each file, attaches `metadata.source` / `metadata.chunk` / `metadata.mtime` / `metadata.ingested_at`, and (by default) replaces any prior chunks for the same source file. Same engine as the CLI below, exposed to the LLM.

| Argument | Type | Description |
| --- | --- | --- |
| `paths` | list[string] | Files or directories on the server host. |
| `collection_name` | string | Target TiDB table. Omitted when `COLLECTION_NAME` is configured. |
| `recursive` | bool (default `false`) | Recurse into directories. |
| `glob` | string (default `*.md`) | Glob applied to directory entries. |
| `chunk_chars` | int (default `2000`) | Max characters per chunk. |
| `overlap` | int (default `200`) | Chunk overlap in characters. |
| `replace` | bool (default `true`) | Delete existing chunks tagged with the same `source` before inserting. |
| `only_modified` | bool (default `false`) | Skip files whose on-disk mtime is not newer than the `metadata.mtime` already stored for the same `source`. Files with no prior record are still processed. Useful for incremental refreshes. |
| `truncate_collection` | bool (default `false`) | `TRUNCATE` the target table before ingesting. The schema is preserved; every input file is then re-chunked and re-embedded. Combining with `only_modified=true` is allowed but pointless — after the truncate there is no prior `mtime` to compare against. |

> `paths` are resolved on the **server** host, not the MCP client. With stdio transport (the default Claude Desktop setup) they share a filesystem, but remote/Docker deployments may not — see "Loading documents into TiDB" for the equivalent CLI which is generally simpler to operate.

This tool is hidden when `TIDB_READ_ONLY=1`.

### `docs-tidb-find`

Performs a similarity search using `VEC_COSINE_DISTANCE`.

| Argument | Type | Description |
| --- | --- | --- |
| `query` | string | What to search for. |
| `collection_name` | string | The TiDB table to search. Omitted when `COLLECTION_NAME` is configured as default. |

Returns the top `TIDB_SEARCH_LIMIT` (default 10) matches ordered by cosine distance ascending.

### `docs-tidb-list`

Lists the documents currently registered in a collection, grouped by `metadata.source`. Use it to inspect what has already been ingested (e.g. before re-ingesting) or to check freshness.

| Argument | Type | Description |
| --- | --- | --- |
| `collection_name` | string | The TiDB table to inspect. Omitted when `COLLECTION_NAME` is configured as default. |

Returns a list of objects, one per distinct `metadata.source` value:

```json
[
  {
    "source": "/abs/path/to/file.md",
    "chunks": 12,
    "mtime": 1700000000.0,
    "ingested_at": 1700000050.5
  }
]
```

`mtime` and `ingested_at` are Unix epoch seconds (or `null` when the metadata key is absent). Rows whose metadata has no `source` key are ignored. Returns an empty list when the collection does not exist yet. This tool stays registered even with `TIDB_READ_ONLY=1`.

## Environment variables

### TiDB connection

| Variable | Default | Description |
| --- | --- | --- |
| `TIDB_HOST` | `127.0.0.1` | TiDB host. |
| `TIDB_PORT` | `4000` | TiDB port. |
| `TIDB_USER` | `root` | TiDB user. |
| `TIDB_PASSWORD` | _(empty)_ | TiDB password. |
| `TIDB_DATABASE` | `test` | Database/schema name. Created automatically on first connect if it does not exist (requires `CREATE DATABASE` privilege). |
| `TIDB_SSL_VERIFY_CERT` | `0` | Set to `1` to enable TLS (required for TiDB Serverless). |
| `TIDB_SSL_CA` | _(unset)_ | Optional CA bundle path, e.g. `/etc/ssl/cert.pem`. |

### Behavior

| Variable | Default | Description |
| --- | --- | --- |
| `COLLECTION_NAME` | _(unset)_ | Default table. When set, the MCP tools drop their `collection_name` argument. |
| `TIDB_SEARCH_LIMIT` | `10` | Max rows returned from `docs-tidb-find`. |
| `TIDB_READ_ONLY` | `0` | When `1`, the `docs-tidb-store` tool is not registered. |
| `TIDB_USE_VECTOR_INDEX` | `1` | When `1`, auto-created tables include an inline `VECTOR INDEX ... USING HNSW` on the embedding column. Requires TiDB v8.4+ and a TiFlash replica in the cluster — see [Vector index](#vector-index). |
| `EMBEDDING_PROVIDER` | `fastembed` | Embedding provider (only `fastembed` is supported today). |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | FastEmbed model name. |
| `TOOL_STORE_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `docs-tidb-store`. |
| `TOOL_FIND_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `docs-tidb-find`. |
| `TOOL_INGEST_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `docs-tidb-ingest`. |
| `TOOL_LIST_DESCRIPTION` | _(see source)_ | Override the description shown to the LLM for `docs-tidb-list`. |
| `TIDB_ALLOW_ARBITRARY_FILTER` | `0` | When `1`, exposes a `query_filter` argument on `docs-tidb-find` that accepts a JSON filter spec. |

## Quick start

### 1. Run TiDB locally

The easiest path on macOS / Linux is `tiup playground`:

```bash
tiup playground

# or specifically with v8.4+ for vector support:
tiup playground v8.5
```

The default endpoint is `127.0.0.1:4000`, user `root`, no password — which matches the defaults of this server.

> Vector search requires TiDB v8.4 or newer. Older versions will fail at `CREATE TABLE` because the `VECTOR` type is unknown.

### 2. Run the MCP server

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

To accept HTTP clients instead of stdio:

```bash
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb --transport streamable-http
# or, legacy SSE
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb --transport sse
```

### 3. Wire it up to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/makocchi-git/mcp-docs-tidb",
        "mcp-docs-tidb"
      ],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "kb"
      }
    }
  }
}
```

### 4. Wire it up to Claude Code

**Option A — CLI (one-shot)**

```bash
claude mcp add mcp-docs-tidb uvx \
  --args "--from,git+https://github.com/makocchi-git/mcp-docs-tidb,mcp-docs-tidb" \
  -e TIDB_HOST=127.0.0.1 \
  -e TIDB_PORT=4000 \
  -e TIDB_USER=root \
  -e TIDB_DATABASE=test \
  -e COLLECTION_NAME=kb
```

This appends the server to your user-global `~/.claude/settings.json`. Append `--scope project` to write to `.claude/settings.json` instead (project-local).

**Option B — edit `settings.json` directly**

User-global (`~/.claude/settings.json`) or project-local (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "mcp-docs-tidb": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/makocchi-git/mcp-docs-tidb",
        "mcp-docs-tidb"
      ],
      "env": {
        "TIDB_HOST": "127.0.0.1",
        "TIDB_PORT": "4000",
        "TIDB_USER": "root",
        "TIDB_PASSWORD": "",
        "TIDB_DATABASE": "test",
        "COLLECTION_NAME": "kb"
      }
    }
  }
}
```

Restart Claude Code (or run `/mcp` to reload without restarting) after editing. Confirm the server is live with `/mcp` — `mcp-docs-tidb` should appear in the list with its four tools.

### 5. Optional: add a vector index

See [Vector index](#vector-index) below. The short version: set `TIDB_USE_VECTOR_INDEX=1` before the first ingest, or run an `ALTER TABLE` later. Either way you need a TiFlash node in the cluster.

## Vector index

`docs-tidb-find` issues `SELECT ... ORDER BY VEC_COSINE_DISTANCE(embedding, ?) LIMIT N`. Without an index, that's a full table scan — fine up to ~10⁴ rows, slow beyond. TiDB supports an [HNSW vector index](https://docs.pingcap.com/ai/vector-search-index/) that turns this into an approximate-nearest-neighbour lookup.

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
ALTER TABLE kb
  ADD VECTOR INDEX idx_embedding
    ((VEC_COSINE_DISTANCE(embedding))) USING HNSW;
```

### Requirements & caveats

- **TiDB v8.4+** (v8.5+ recommended). Older versions don't support the `VECTOR INDEX` syntax.
- **A TiFlash replica is required.** TiDB allocates one automatically at index creation, but the cluster must actually have a TiFlash node — otherwise the `CREATE TABLE` or `ALTER TABLE` fails. `tiup playground` includes TiFlash by default; some self-hosted deployments and minimal Docker setups don't. Check with `SELECT type FROM information_schema.cluster_info WHERE type='tiflash'`.
- The index uses **cosine distance** (`VEC_COSINE_DISTANCE`). If you need `VEC_L2_DISTANCE` instead, do not use this flag — drop the auto index and create your own via `ALTER TABLE`.
- The index is **read-side only**: writes still go through TiKV; reads use TiFlash. Expect a brief lag before fresh inserts are searchable through the index.

Without an index, `docs-tidb-find` still works — it just performs a full scan.

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

1. **MCP tool `docs-tidb-ingest`** — ask the LLM to "load `~/docs` into the `kb` collection". The server reads the files itself and writes the chunks. Good for interactive use from Claude Desktop / Cursor / Windsurf when the MCP server and client share a filesystem.
2. **`mcp-docs-tidb-ingest` CLI** — run outside of any LLM conversation, e.g. from cron or CI to refresh a corpus. Same code path as the MCP tool; choose whichever fits your workflow.

### CLI

```bash
# Ingest every Markdown file under ./docs into the `kb` table.
TIDB_HOST=127.0.0.1 TIDB_PORT=4000 TIDB_USER=root TIDB_DATABASE=test \
  uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
    --collection kb \
    --recursive --glob '*.md' \
    ./docs

# Re-run after editing — same files get replaced atomically per file.
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' ./docs

# Incremental refresh: skip files whose on-disk mtime is not newer than the
# value already stored in TiDB. Ideal for cron-driven refreshes of a large
# corpus where only a handful of files change between runs.
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' --only-modified ./docs

# Full rebuild: wipe every row first, then re-ingest everything below ./docs.
# Useful after large-scale edits or to recover from inconsistent state.
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' --truncate ./docs

# Tag every chunk with extra metadata (useful with filterable fields).
# Values that parse as valid JSON (numbers, booleans) are decoded automatically.
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' \
  --extra-metadata category=docs \
  --extra-metadata public=true \
  ./docs

# Exclude files matching a glob pattern (filename or full path).
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb-ingest \
  --collection kb --recursive --glob '*.md' \
  --exclude-glob 'CHANGELOG.md' \
  --exclude-glob '*/drafts/*' \
  ./docs
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
| `--only-modified` | off | Skip files whose on-disk mtime is not newer than the `metadata.mtime` recorded for the same source. Files with no prior record are still processed. |
| `--truncate` | off | `TRUNCATE TABLE` the collection before ingesting. Schema is kept; every row is wiped, then the inputs are re-chunked and re-embedded. Use to rebuild from scratch. |
| `--extra-metadata` | _(unset)_ | Extra `KEY=VALUE` metadata pair attached to every ingested chunk. May be repeated. Values that are valid JSON (numbers, booleans, arrays, objects) are decoded automatically; anything else is stored as a string. Standard fields (`source`, `chunk`, `mtime`, `ingested_at`) always take precedence over conflicting keys. |
| `--exclude-glob` | _(unset)_ | Glob pattern for files to skip. May be repeated. Matched against both the filename and the full path (e.g. `--exclude-glob 'CHANGELOG.md'` or `--exclude-glob '*/drafts/*'`). |
| `-v`, `--verbose` | off | Log per-file progress (incl. which files were skipped by `--only-modified`). |

### What gets written

For each input file, the CLI:

1. Reads the file as UTF-8.
2. Splits it into character-based chunks of `--chunk-chars` with `--overlap` overlap.
3. (By default) deletes existing rows whose `metadata.source` equals the absolute path of this file.
4. Inserts one row per chunk with `metadata = {"source": "<abs path>", "chunk": <0-based index>, "mtime": <file mtime, epoch s>, "ingested_at": <now, epoch s>, ...}`. Any `--extra-metadata` pairs are merged in first; the four standard fields always win if there is a key conflict.

So a re-ingest of the same file produces the same row count regardless of how many times you've run it — useful for cron-driven refreshes. All chunks of a single ingest share the same `ingested_at`; `mtime` is the file's on-disk modification time at ingest time.

### Re-ingest semantics

- **Default (replace per source)**: only the affected file's chunks are removed. Other files in the same collection are untouched.
- **`--no-replace`**: previously-ingested chunks stay in place; new chunks are added. Use this only if you actually want versioned history.
- **`--only-modified` (incremental)**: each input file's on-disk mtime is compared against the largest `metadata.mtime` already stored for the same `source`. Files where the on-disk mtime is not strictly greater are skipped — nothing is read, embedded, or written for them. Files with no prior record are always processed. Mutually compatible with `--no-replace`, but the common pairing is the default `replace=true` + `--only-modified`. Mind that this relies on the source file's mtime being trustworthy (e.g. some build steps or `git checkout` may rewrite mtimes).
- **`--truncate` (full rebuild)**: every row of the collection is removed via `TRUNCATE TABLE` *before* any input file is read. The table schema (including the `VECTOR(<dim>)` column and any indexes) is kept, so this is cheaper than `DROP TABLE` + first-ingest. Use it when the corpus shape has changed enough that incremental re-ingest would leave stale chunks behind (e.g. files were removed from the input directory). `--truncate` and `--no-replace` can coexist — `--no-replace` becomes a no-op since the truncate already cleared the slate.
- **Schema change** (e.g. switching embedding models with a different dim): the CLI cannot recover from this — `DROP TABLE <collection>` first, then re-ingest. `--truncate` is not enough because the `VECTOR(<dim>)` column type is baked in.

### Python API

`mcp-docs-tidb-ingest` is a thin wrapper around `mcp_docs_tidb.ingest.ingest_paths`, which you can call directly if you need to embed ingestion into your own pipeline:

```python
from pathlib import Path

from mcp_docs_tidb.embeddings.factory import create_embedding_provider
from mcp_docs_tidb.ingest import collect_paths, ingest_paths
from mcp_docs_tidb.settings import EmbeddingProviderSettings, TiDBSettings
from mcp_docs_tidb.tidb import TiDBConnector

connector = TiDBConnector(
    settings=TiDBSettings(),
    embedding_provider=create_embedding_provider(EmbeddingProviderSettings()),
)
try:
    files = collect_paths([Path("docs")], recursive=True, glob="*.md")
    n = ingest_paths(
        files,
        collection_name="kb",
        connector=connector,
        chunk_chars=1500,
        overlap=150,
        only_modified=True,  # skip files whose mtime hasn't advanced
        extra_metadata={"team": "platform"},
    )
    print(f"wrote {n} chunks")
finally:
    connector.close()
```

`extra_metadata` is merged into every chunk's metadata, alongside the standard `source` / `chunk` / `mtime` / `ingested_at` keys. Combined with [filterable fields](#filtering-search-results) you can then filter `docs-tidb-find` by, e.g., `team`.

## Filtering search results

`docs-tidb-find` supports filtering on values inside the `metadata` JSON column. Two mechanisms are available — pick at most one per deployment.

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

`docs-tidb-find` is then exposed to the LLM with the typed arguments `category: str | None`, `year: int | None`, `tags: list[str] | None`.

Supported `field_type` × `condition` combinations:

| `field_type` | Allowed `condition` |
| --- | --- |
| `keyword` | `==`, `!=`, `any`, `except` |
| `integer` | `==`, `!=`, `>`, `>=`, `<`, `<=`, `any`, `except` |
| `float` | `>`, `>=`, `<`, `<=` |
| `boolean` | `==`, `!=` |

If `condition` is omitted, the field is still indexed but no argument is exposed to the LLM.

### Option B. Arbitrary JSON filter

Set `TIDB_ALLOW_ARBITRARY_FILTER=1` to expose a `query_filter` argument on `docs-tidb-find`. The value is a JSON object:

```json
{
  "must":     [{"field": "category", "op": "==", "value": "work"}],
  "must_not": [{"field": "archived", "op": "==", "value": "true"}]
}
```

Supported `op` values: `==`, `!=`, `>`, `>=`, `<`, `<=`, `in`, `not_in`.

This mode does not require declaring fields up-front (and does not create indexes), so it is useful for ad-hoc exploration but slower on large tables.

## Connecting to TiDB Cloud Starter

TiDB Cloud Starter requires TLS. Point `TIDB_SSL_CA` at your system CA bundle:

```bash
TIDB_HOST=gateway01.us-west-2.prod.aws.tidbcloud.com \
TIDB_PORT=4000 \
TIDB_USER='xxxxx.root' \
TIDB_PASSWORD='your-password' \
TIDB_DATABASE='test' \
TIDB_SSL_VERIFY_CERT=1 \
TIDB_SSL_CA=/etc/ssl/cert.pem \
COLLECTION_NAME=kb \
uvx --from git+https://github.com/makocchi-git/mcp-docs-tidb mcp-docs-tidb
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src
uv run mypy src
```

## License

MIT License.
