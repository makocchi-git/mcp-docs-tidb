# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

```bash
uv sync                     # install / refresh dependencies
uv run pytest               # run all tests
uv run pytest tests/test_filters.py  # run a single test file
uv run pytest -k "test_name"         # run a single test by name
uv run ruff check src       # lint
uv run mypy src             # type-check
uv run mcp-docs-tidb        # start MCP server (stdio)
uv run mcp-docs-tidb --transport streamable-http  # HTTP transport
uv run mcp-docs-tidb-ingest --collection kb --recursive --glob '*.md' ./docs  # CLI ingest
```

Integration tests (`tests/test_tidb_integration.py`) require a live TiDB instance and are skipped automatically when `127.0.0.1:4000` is unreachable.

## Architecture

```
src/mcp_docs_tidb/
├── main.py            # CLI entrypoint — parses --transport and calls TiDBMCPServer.run()
├── mcp_server.py      # TiDBMCPServer (extends FastMCP) — registers docs-tidb-store / docs-tidb-find / docs-tidb-ingest tools
├── tidb.py            # TiDBConnector — aiomysql connection pool, CRUD, auto-CREATE TABLE
├── settings.py        # Pydantic-settings classes: TiDBSettings, ToolSettings, EmbeddingProviderSettings, FilterableField
├── ingest.py          # Chunking + bulk-write logic; also the mcp-docs-tidb-ingest CLI entrypoint
├── embeddings/
│   ├── base.py        # EmbeddingProvider ABC
│   ├── fastembed.py   # FastEmbed implementation (default)
│   └── factory.py     # create_embedding_provider()
└── common/
    ├── filters.py     # SQL WHERE clause builders (arbitrary + declared filterable fields)
    ├── func_tools.py  # make_partial_function — removes parameters from a function's signature
    └── wrap_filters.py # wrap_filters — wraps find() with typed per-field filter arguments
```

**Key design points:**

- Each "collection" is a TiDB table, auto-created on first write with `VECTOR(<dim>)`, `JSON metadata`, and optional `VIRTUAL` generated columns for indexed filterable fields.
- The embedding dimension is baked into the `VECTOR` column at table creation and cannot be changed without dropping the table.
- `make_partial_function` / `wrap_filters` are used in `setup_tools()` to dynamically reshape the FastMCP tool signatures based on runtime config: hide `collection_name` when `COLLECTION_NAME` env is set, swap out `query_filter` for typed per-field arguments when `filterable_fields` are declared.
- Filterable fields become `VIRTUAL AS (JSON_UNQUOTE(JSON_EXTRACT(metadata, '$.field')))` generated columns with a `KEY` index. The HNSW vector index requires TiDB v8.4+ and a TiFlash node.
- Tests use `DeterministicEmbeddingProvider` (SHA-256 hash → normalised vector) to avoid the multi-hundred-MB FastEmbed download. Integration tests are gated by `requires_tidb` mark.

## Environment

Copy `.env.example` to `.env` and fill in TiDB connection details. For TiDB Serverless, set `TIDB_SSL_VERIFY_CERT=1` and `TIDB_SSL_CA=/etc/ssl/cert.pem`.

## Coding guidelines

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
