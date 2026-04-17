"""Entry point for atlas-slack MCP server."""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Atlas Slack MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    args = parser.parse_args()

    from atlas_slack.gateway import mcp
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
