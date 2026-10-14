"""The agent loop, written against an MCP client rather than local functions.

The agent discovers tools from the server at runtime, so the same loop works with any MCP
server. Two client-side safety nets sit on top of whatever the server enforces:
- tools the server marks as not read-only need approval before they run;
- a hard step limit stops runaway loops.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mcp import Client

from .providers import Message, Provider

SYSTEM = """You are a support operations agent working through a helpdesk's MCP tools.
Use tools to look things up rather than guessing. Prefer several tool calls in one turn when they are independent.
Ground every claim about policy in a knowledge base article. Keep internal notes short and specific.
Finish with a brief plain-text summary of what you found and what you changed."""


@dataclass
class Event:
    kind: str  # "text" | "tool_call" | "tool_result" | "denied" | "done" | "error"
    data: dict[str, Any]


@dataclass
class RunResult:
    output: str
    events: list[Event] = field(default_factory=list)
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    duration_s: float = 0.0
    ok: bool = True


Approver = Callable[[str, dict[str, Any]], Awaitable[bool]]


async def approve_all(_name: str, _args: dict[str, Any]) -> bool:
    return True


def _result_text(result: Any) -> str:
    """Prefer the structured result, which is exact JSON, over the human-readable text blocks."""
    if result.structured_content is not None:
        return json.dumps(result.structured_content, default=str)
    return "\n".join(getattr(c, "text", "") for c in result.content)


async def run_agent(
    client: Client,
    task: str,
    provider: Provider,
    *,
    approve_writes: Approver = approve_all,
    max_steps: int = 12,
    max_tokens: int = 2048,
    on_event: Callable[[Event], None] | None = None,
) -> RunResult:
    started = time.perf_counter()
    listed = (await client.list_tools()).tools
    tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in listed]
    read_only = {t.name for t in listed if t.annotations and t.annotations.read_only_hint}
    messages: list[Message] = [{"role": "user", "content": task}]
    result = RunResult(output="")

    def emit(kind: str, **data: Any) -> None:
        event = Event(kind, data)
        result.events.append(event)
        if on_event:
            on_event(event)

    for step in range(1, max_steps + 1):
        result.steps = step
        turn = await provider.complete(system=SYSTEM, messages=messages, tools=tools, max_tokens=max_tokens)
        result.input_tokens += turn.input_tokens
        result.output_tokens += turn.output_tokens
        messages.append({"role": "assistant", "content": turn.content})

        text = "\n".join(b["text"] for b in turn.content if b["type"] == "text").strip()
        if text:
            emit("text", text=text)
        calls = [b for b in turn.content if b["type"] == "tool_use"]
        if not calls or turn.stop_reason != "tool_use":
            result.output = text
            result.duration_s = time.perf_counter() - started
            emit("done", output=text, steps=step)
            return result

        results: list[dict[str, Any]] = []
        for call in calls:
            name, args = call["name"], call["input"]
            emit("tool_call", name=name, args=args)
            if name not in read_only and not await approve_writes(name, args):
                content, is_error = "A person declined this action. Don't retry it; explain what you would have done.", True
                emit("denied", name=name, args=args)
            else:
                res = await client.call_tool(name, args)
                content, is_error = _result_text(res), bool(res.is_error)
                emit("tool_result", name=name, content=content, is_error=is_error)
            results.append({"type": "tool_result", "tool_use_id": call["id"], "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    result.ok = False
    result.output = f"Stopped after {max_steps} steps without finishing."
    result.duration_s = time.perf_counter() - started
    emit("error", message=result.output)
    return result
