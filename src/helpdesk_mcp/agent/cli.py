"""helpdesk-agent: run the agent against the helpdesk MCP server.

    helpdesk-agent "Triage the urgent open tickets"
    helpdesk-agent --yes "Triage ticket 4"           # approve write tools automatically
    helpdesk-agent --read-only "Triage ticket 4"     # server exposes no write tools

The server is launched as a subprocess and spoken to over stdio, exactly as Claude Desktop would.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from .loop import Event, run_agent
from .providers import default_provider

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def print_event(event: Event) -> None:
    d = event.data
    if event.kind == "text":
        print(paint("35", "model  ") + d["text"])
    elif event.kind == "tool_call":
        print(paint("95", "call   ") + f"{d['name']}({short(d['args'], 120)})")
    elif event.kind == "tool_result":
        label = paint("31", "error  ") if d["is_error"] else paint("36", "result ")
        print(label + short(d["content"]))
    elif event.kind == "denied":
        print(paint("33", "denied ") + d["name"])
    elif event.kind == "error":
        print(paint("31", "stop   ") + d["message"])


async def ask(name: str, args: dict[str, Any]) -> bool:
    prompt = f"\nAllow write tool {name}({short(args, 200)})? [y/N] "
    answer = await asyncio.to_thread(input, prompt)
    return answer.strip().lower() in {"y", "yes"}


async def approve(name: str, args: dict[str, Any]) -> bool:
    return True


async def amain(args: argparse.Namespace) -> int:
    server_args = ["-m", "helpdesk_mcp", "--db", str(args.db)]
    if args.read_only:
        server_args.append("--read-only")
    params = StdioServerParameters(command=sys.executable, args=server_args, env=dict(os.environ))
    provider = default_provider()
    print(paint("2", f"model: {provider.model}   server: helpdesk-mcp over stdio   db: {args.db}\n"))

    async with Client(params) as client:
        result = await run_agent(
            client,
            args.task,
            provider,
            approve_writes=approve if args.yes else ask,
            max_steps=args.max_steps,
            on_event=print_event,
        )
    tokens = result.input_tokens + result.output_tokens
    print(paint("2", f"\n{result.steps} model turns, {tokens:,} tokens, {result.duration_s:.1f}s"))
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="helpdesk-agent", description="Run the support agent against the helpdesk MCP server.")
    parser.add_argument("task", nargs="?", default="Triage the urgent open tickets")
    parser.add_argument("--db", type=Path, default=Path.home() / ".helpdesk-mcp" / "helpdesk.db")
    parser.add_argument("--yes", action="store_true", help="Approve write tools without asking.")
    parser.add_argument("--read-only", action="store_true", help="Start the server with write tools disabled.")
    parser.add_argument("--max-steps", type=int, default=12)
    sys.exit(asyncio.run(amain(parser.parse_args(argv))))


if __name__ == "__main__":
    main()
