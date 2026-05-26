import argparse
import logging
import sys


def main() -> None:
    """
    Entry point for the `mcp-docs-tidb` console script declared in
    pyproject.toml. It parses the transport flag and then starts the
    FastMCP server.
    """
    # Configure logging before importing server so startup errors reach stderr,
    # not stdout (stdio transport would corrupt the MCP stream otherwise).
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    parser = argparse.ArgumentParser(description="mcp-docs-tidb")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    args = parser.parse_args()

    # Import here so that environment-driven settings are evaluated after
    # argument parsing (matches the upstream qdrant server's behaviour).
    try:
        from mcp_docs_tidb.server import mcp
    except Exception as exc:
        sys.stderr.write(
            f"ERROR: Failed to start mcp-docs-tidb server: {exc}\n"
            "Check your environment variables (TIDB_HOST, TIDB_PORT, etc.).\n"
        )
        sys.exit(1)

    try:
        mcp.run(transport=args.transport)
    finally:
        connector = getattr(mcp, "tidb_connector", None)
        if connector is not None:
            connector.close()


if __name__ == "__main__":
    main()
