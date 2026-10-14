"""Run the helpdesk MCP server.

    helpdesk-mcp                         # stdio, for Claude Desktop and other MCP clients
    helpdesk-mcp --read-only             # no write tools at all
    helpdesk-mcp --http --port 8000      # Streamable HTTP at http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .seed import seed
from .server import build_server
from .store import HelpdeskStore

DEFAULT_DB = Path.home() / ".helpdesk-mcp" / "helpdesk.db"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="helpdesk-mcp", description="Helpdesk MCP server.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file (default: {DEFAULT_DB}). Use :memory: for a throwaway database.")
    parser.add_argument("--read-only", action="store_true", help="Expose read tools only.")
    parser.add_argument("--no-seed", action="store_true", help="Don't fill an empty database with demo data.")
    parser.add_argument("--http", action="store_true", help="Serve Streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    # stdout is the MCP channel in stdio mode, so logs must go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    if str(args.db) != ":memory:":
        args.db.parent.mkdir(parents=True, exist_ok=True)
    store = HelpdeskStore(args.db)
    if not args.no_seed:
        seed(store)

    server = build_server(store, read_only=args.read_only)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
