import argparse


def main() -> None:
    """
    Entry point for the `mcp-docs-tidb` console script declared in
    pyproject.toml. It parses the transport flag and then starts the
    FastMCP server.
    """
    parser = argparse.ArgumentParser(description="mcp-docs-tidb")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    args = parser.parse_args()

    # Import here so that environment-driven settings are evaluated after
    # argument parsing (matches the upstream qdrant server's behaviour).
    from mcp_docs_tidb.server import mcp

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
