---
name: tidb-docs
description: Use this skill when the user wants to index, search, or maintain a corpus of local documents in a TiDB vector store via the mcp-docs-tidb project. Triggers include "ingest these docs into TiDB", "find X in the TiDB knowledge base", "list documents registered in TiDB", "refresh the kb collection", "set up TiDB document search", or any task that involves the `docs-tidb-ingest`, `docs-tidb-find`, `docs-tidb-list`, or `docs-tidb-store` MCP tools, or the `mcp-docs-tidb-ingest` CLI.
---

# TiDB document store skill

This skill drives the `mcp-docs-tidb` project: a vector-search backend that stores chunked documents in TiDB and exposes them to the LLM through MCP tools (`docs-tidb-ingest`, `docs-tidb-find`, `docs-tidb-list`, `docs-tidb-store`) and an equivalent CLI (`mcp-docs-tidb-ingest`).

Use it when the user asks to load documents into TiDB, search them, or maintain a knowledge base — *not* for ad-hoc memories during a conversation (those go through `docs-tidb-store` directly with no skill involvement).

## Prerequisites — check first

Before doing any indexing or search work, verify:

1. **TiDB is reachable.** `nc -z $TIDB_HOST $TIDB_PORT`, or `mysql -h $TIDB_HOST -P $TIDB_PORT -u $TIDB_USER -e "SELECT VERSION()"`. The version must be **v8.4 or newer** — older TiDB lacks the `VECTOR` type and `CREATE TABLE` will fail. TiDB Serverless also works (set `TIDB_SSL_VERIFY_CERT=1`).
2. **Which collection?** Ask the user, or default to `kb` if they haven't said. The name must match `^[A-Za-z_][A-Za-z0-9_]*$` — anything else is rejected at runtime.
3. **Which embedding model?** Default `sentence-transformers/all-MiniLM-L6-v2` (384 dim) is fine for English; for multilingual or larger corpora suggest `BAAI/bge-base-en-v1.5` (768) or `bge-m3` (1024). **Dimensions are baked into the table** — changing the model later requires `DROP TABLE <collection>` first.

If TiDB is unreachable, stop and ask the user to start it (`tiup playground v8.4.0`) before continuing.

## Picking the right route

| User intent | Use |
| --- | --- |
| "Index/load/refresh these files into TiDB" | `docs-tidb-ingest` MCP tool (if available in this session) or `mcp-docs-tidb-ingest` CLI |
| "Find / search / what does X say about Y" | `docs-tidb-find` MCP tool |
| "What's already registered / list documents / show sources / when was X last ingested" | `docs-tidb-list` MCP tool |
| "Remember this single fact / note" | `docs-tidb-store` MCP tool (not bulk) |
| Automated/cron refresh outside a conversation | `mcp-docs-tidb-ingest` CLI |
| Remote / Dockerised MCP server, filesystem not shared with the LLM client | CLI on the *server* host (the MCP tool would not see the client's files) |

Never use `docs-tidb-store` in a loop to populate a corpus — it is for single notes. Use `docs-tidb-ingest` instead, which chunks and tags with `metadata.source` for safe re-ingest.

## Workflow: indexing a corpus

1. **Confirm the inputs.** Ask which paths, recursive or not, and which glob (default `*.md`). If the user gestures at a directory, default to recursive.
2. **Check dimensions match.** If the collection already exists, run `SHOW CREATE TABLE <collection>` (via `mysql` or any MCP DB tool the user has) to confirm `VECTOR(<dim>)` matches the configured model. Mismatch means **drop and re-create**, not partial fix.
3. **Run the ingest.** Prefer the MCP tool when available; otherwise:
   ```bash
   TIDB_HOST=... TIDB_USER=... TIDB_DATABASE=... \
     uv run mcp-docs-tidb-ingest \
       --collection <name> --recursive --glob '*.md' <paths...>
   ```
   Re-running the same command is safe — chunks are replaced per source file by default. Pass `--no-replace` only if the user explicitly wants append-only history. For periodic refreshes of a large corpus where only a few files change, add `--only-modified` (or the `only_modified=True` argument on the MCP tool) so unchanged files are skipped based on their on-disk mtime vs. the `metadata.mtime` already in TiDB.
4. **Report counts.** The tool/CLI returns "Ingested N chunk(s) from M file(s)". Surface that to the user verbatim — it's the only direct signal that re-ingest actually replaced rows.
5. **Vector index decision.** Two paths, requires TiFlash in either case:
   - Set `TIDB_USE_VECTOR_INDEX=1` *before* the first ingest so the auto-created table includes `VECTOR INDEX ... USING HNSW` inline. Cleanest, but no-op against existing tables.
   - Run `ALTER TABLE <collection> ADD VECTOR INDEX idx_embedding ((VEC_COSINE_DISTANCE(embedding))) USING HNSW` after the first sizeable load. Use this when the table already exists.
   Without an index, searches are sequential scans — fine up to ~10⁴ rows, painful beyond. If the cluster has no TiFlash node, skip this step; index creation will fail.

## Workflow: inspecting registered documents

- Call `docs-tidb-list` to enumerate what is already in a collection. Each row is `{source, chunks, mtime, ingested_at}` (Unix epoch seconds, or `null` when the metadata key is missing).
- Use it before re-ingesting to confirm the collection name is correct and to spot stale or missing sources.
- The tool is read-only and stays available even when `TIDB_READ_ONLY=1`.
- An empty list either means the collection has not been created yet, or every row lacks a `metadata.source` (e.g. populated only via `docs-tidb-store` with no source). Confirm with `SELECT COUNT(*) FROM <collection>` if uncertain.

## Workflow: searching

- Call `docs-tidb-find` with the user's natural-language query. Do **not** pre-translate it to keywords — the embedding is the point.
- Results come back as `<entry><content>...</content><metadata>{"source": "...", "chunk": N, "mtime": ..., "ingested_at": ...}</metadata></entry>`. `mtime` and `ingested_at` are Unix epoch seconds (floats). When citing back to the user, include the `source` path so they can verify. If freshness matters, surface `mtime` too.
- If the result set is empty:
  - First confirm the collection has rows (`SELECT COUNT(*) FROM <collection>`).
  - Then check `TIDB_SEARCH_LIMIT` is not 0/negative.
  - Then consider whether the user really ingested into this collection (typos in name).

## Filtering

Two mechanisms — use at most one per deployment.

- **Filterable fields (preferred for known schemas).** Declared up-front in `TiDBSettings(filterable_fields=[...])`; each becomes a typed MCP argument and a `VIRTUAL` indexed column. Use this when the corpus has structured metadata you'll filter on repeatedly (e.g. `team`, `year`).
- **Arbitrary JSON filter.** `TIDB_ALLOW_ARBITRARY_FILTER=1` exposes a `query_filter` argument shaped like `{"must": [{"field": "...", "op": "==", "value": "..."}]}`. Useful for ad-hoc exploration, no index required, but slower on large tables.

If the user is mixing both, push back — pick one.

## Workflow: refreshing / removing

- **Re-ingest a file**: just run `docs-tidb-ingest` again. Old chunks for that source are deleted first.
- **Periodic refresh of a directory**: add `--only-modified` (CLI) or `only_modified=True` (MCP tool). Each file's on-disk mtime is compared against the stored `metadata.mtime` and unchanged files are skipped entirely. Files not yet in TiDB are always processed.
- **Full rebuild (same schema)**: add `--truncate` (CLI) or `truncate_collection=True` (MCP tool). The table is `TRUNCATE`d before any input is processed, then every file is re-chunked and re-embedded. Use this when files were *removed* from the source directory (`--only-modified` won't notice deletions) or to recover from inconsistent state. Cheaper than `DROP TABLE` because the schema and any vector indexes are preserved.
- **Delete a file's chunks without re-ingesting**: there is no MCP tool for this; use `mysql` directly:
  ```sql
  DELETE FROM <collection>
   WHERE JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.source')) = '/abs/path/to/file.md';
  ```
- **Wipe and rebuild**: `DROP TABLE <collection>;` then re-ingest. Required when changing embedding models with a different dimension. For same-schema rebuilds (e.g. files removed from the source), prefer `--truncate` / `truncate_collection=True` — it keeps the schema and indexes.

## Common pitfalls

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Invalid TiDB table identifier` | Collection name has `-`, `.`, spaces, etc. | Use only `[A-Za-z_][A-Za-z0-9_]*`. |
| `VECTOR(...) ... type does not exist` | TiDB < v8.4 | Upgrade TiDB or use TiDB Serverless. |
| `Vector dimension mismatch` on insert | Embedding model changed after the table was created | `DROP TABLE` and re-ingest, or use a new collection name. |
| `VECTOR INDEX` creation fails | Cluster has no TiFlash node | Either set `TIDB_USE_VECTOR_INDEX=0` (skip the index) or add a TiFlash replica. Check with `SELECT type FROM information_schema.cluster_info WHERE type='tiflash'`. |
| `Access denied` | OS user leaked into `TIDB_USER` default | Set `TIDB_USER=root` (or the actual user) explicitly. |
| Find returns nothing in a non-empty table | Search hit a different collection, or `TIDB_READ_ONLY=1` (no `docs-tidb-store`/`docs-tidb-ingest` registered — but `docs-tidb-find` still works against existing data) | Confirm collection name; `SELECT COUNT(*) FROM <collection>`. |
| MCP tool says "path not found" | `paths` are resolved on the *server* host, not the client | Use the CLI on the server host, or share the filesystem (stdio transport already does). |

## Tone of responses

- When indexing, name the collection and source paths so the user can spot mistakes.
- When citing search results, always include the `metadata.source` path. Never present an answer derived from `docs-tidb-find` results as if it were yours.
- Don't invent collections, columns, or environment variables that aren't in this skill — fall back to "let me check the README" when unsure.

## Project pointers

- Main MCP server entry: `src/mcp_docs_tidb/main.py` → `server.py` → `mcp_server.py`
- Storage layer: `src/mcp_docs_tidb/tidb.py` (vector DDL, `VEC_FROM_TEXT`, `VEC_COSINE_DISTANCE`)
- Ingestion: `src/mcp_docs_tidb/ingest.py` (shared by the MCP tool and the CLI)
- Filter helpers: `src/mcp_docs_tidb/common/filters.py`
- Full reference: `README.md`
