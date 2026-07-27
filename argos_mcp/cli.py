"""Command-line entry point for Argos.

Exposes two transports:
    argos serve   - HTTP (Streamable HTTP) MCP server, the default.
    argos stdio   - stdio MCP server, handy for SSH or local clients with no
                    open port (the SSH key is the authentication).
"""

import argparse
import os

from argos_mcp.server import init_db, mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="argos",
        description="Argos - central project memory MCP server.",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the HTTP MCP server (default).")
    serve.add_argument(
        "--host",
        default=os.environ.get("MC_HOST", "127.0.0.1"),
        help="Host to bind (env: MC_HOST, default 127.0.0.1).",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MC_PORT", "8765")),
        help="Port to bind (env: MC_PORT, default 8765).",
    )

    sub.add_parser(
        "stdio",
        help="Run the MCP server over stdio (for SSH / local clients).",
    )

    args = parser.parse_args()

    init_db()

    if args.command == "stdio":
        mcp.run(transport="stdio")
        return

    # Bare `argos` behaves like `argos serve`; fall back to env defaults.
    host = getattr(args, "host", None) or os.environ.get("MC_HOST", "127.0.0.1")
    port = getattr(args, "port", None) or int(os.environ.get("MC_PORT", "8765"))
    if not os.environ.get("MC_TOKEN"):
        print("WARNING: MC_TOKEN is empty - auth is disabled. Use only behind localhost/VPN/SSH.")
    print(f"Argos -> http://{host}:{port}/mcp")
    mcp.run(transport="http", host=host, port=port)


if __name__ == "__main__":
    main()
