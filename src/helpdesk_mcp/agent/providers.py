"""Model providers for the agent.

Messages use the Anthropic Messages API shape (text / tool_use / tool_result blocks), so the
real provider passes them through unchanged and the demo provider reads the same structure.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]
Block = dict[str, Any]


@dataclass
class ModelTurn:
    content: list[Block]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


class Provider(Protocol):
    model: str

    async def complete(self, *, system: str, messages: list[Message], tools: list[dict[str, Any]], max_tokens: int) -> ModelTurn: ...


class AnthropicProvider:
    """Claude through the official SDK. Imported lazily so the demo needs no extra install."""

    def __init__(self, model: str | None = None, client: Any = None) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        if client is None:
            from anthropic import AsyncAnthropic  # noqa: PLC0415 optional at import time

            client = AsyncAnthropic()
        self._client = client

    async def complete(self, *, system: str, messages: list[Message], tools: list[dict[str, Any]], max_tokens: int) -> ModelTurn:
        response = await self._client.messages.create(
            model=self.model, system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )
        content: list[Block] = []
        for block in response.content:
            if block.type == "text":
                content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        return ModelTurn(content, response.stop_reason or "end_turn", response.usage.input_tokens, response.usage.output_tokens)


@dataclass
class _History:
    """What the demo model can see: every tool call it made and what came back."""

    calls: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    results: list[tuple[str, dict[str, Any], Any, bool]] = field(default_factory=list)

    @classmethod
    def read(cls, messages: list[Message]) -> _History:
        h = cls()
        for m in messages:
            if not isinstance(m["content"], list):
                continue
            for b in m["content"]:
                if b["type"] == "tool_use":
                    h.calls[b["id"]] = (b["name"], b["input"])
                elif b["type"] == "tool_result":
                    name, args = h.calls[b["tool_use_id"]]
                    try:
                        payload = json.loads(b["content"])
                    except (TypeError, ValueError):
                        payload = b["content"]
                    h.results.append((name, args, payload, bool(b.get("is_error"))))
        return h

    def of(self, name: str) -> list[tuple[dict[str, Any], Any]]:
        return [(args, payload) for n, args, payload, err in self.results if n == name and not err]


class DemoProvider:
    """A scripted, deterministic stand-in for a model, so the agent runs with no API key and CI is stable.

    It knows one job well: triage. Given "triage ticket 4" or "triage the urgent open tickets"
    it searches, reads each ticket, finds a knowledge base article, and leaves an internal note,
    using several tool calls per turn like a real model would.
    """

    model = "demo-offline"

    async def complete(self, *, system: str, messages: list[Message], tools: list[dict[str, Any]], max_tokens: int) -> ModelTurn:
        task = messages[0]["content"] if isinstance(messages[0]["content"], str) else ""
        available = {t["name"] for t in tools}
        h = _History.read(messages)
        turn = self._next(task, available, h, len(h.calls))
        stop = "tool_use" if any(b["type"] == "tool_use" for b in turn) else "end_turn"
        size = len(json.dumps(messages))
        return ModelTurn(turn, stop, input_tokens=size // 4, output_tokens=len(json.dumps(turn)) // 4)

    def _next(self, task: str, available: set[str], h: _History, n: int) -> list[Block]:
        def call(name: str, args: dict[str, Any], i: int) -> Block:
            return {"type": "tool_use", "id": f"toolu_demo_{n + i}", "name": name, "input": args}

        ids = [int(x) for x in re.findall(r"ticket\s*#?(\d+)", task, re.I)]
        if not re.search(r"triage", task, re.I):
            return [{"type": "text", "text": "The offline demo model can only triage tickets, for example "
                     "'Triage the urgent open tickets' or 'Triage ticket 4'. Set ANTHROPIC_API_KEY for anything else."}]

        tickets = [p for _, p in h.of("get_ticket")]
        if not ids and not h.of("search_tickets") and not tickets:
            return [
                {"type": "text", "text": "I'll find the urgent open tickets first."},
                call("search_tickets", {"status": ["open"], "priority": ["urgent"], "limit": 3}, 0),
            ]
        if not tickets:
            found = ids or [t["id"] for _, p in h.of("search_tickets") for t in p.get("result", [])]
            if not found:
                return [{"type": "text", "text": "There are no matching tickets to triage."}]
            return [{"type": "text", "text": f"Reading {len(found)} ticket(s)."}] + [
                call("get_ticket", {"ticket_id": tid}, i) for i, tid in enumerate(found)
            ]
        searched = {args["query"] for args, _ in h.of("search_kb")}
        if not searched:
            return [{"type": "text", "text": "Checking the knowledge base for each one."}] + [
                call("search_kb", {"query": t["subject"][:200]}, i) for i, t in enumerate(tickets)
            ]

        best = {args["query"]: (p.get("result") or [None])[0] for args, p in h.of("search_kb")}
        attempted = any(name == "add_comment" for name, _ in h.calls.values())
        declined = [a for n, a, _, err in h.results if n == "add_comment" and err]
        if "add_comment" in available and not attempted:
            return [
                call("add_comment", {"ticket_id": t["id"], "body": self._note(t, best.get(t["subject"])), "internal": True}, i)
                for i, t in enumerate(tickets)
            ]

        lines = ["Triage summary:"]
        for t in tickets:
            hit = best.get(t["subject"])
            article = f"article `{hit['slug']}`" if hit else "no matching article"
            lines.append(f"- #{t['id']} {t['subject']} ({t['priority']}, {t['category']}): {article}.")
        if "add_comment" not in available:
            lines.append("Read-only mode: no notes were written.")
        elif declined:
            lines.append(f"{len(declined)} note(s) were not written because the action was declined. I didn't retry.")
        else:
            lines.append("I left an internal note on each ticket. Nothing was sent to customers.")
        return [{"type": "text", "text": "\n".join(lines)}]

    @staticmethod
    def _note(ticket: dict[str, Any], hit: dict[str, Any] | None) -> str:
        plan = ticket["customer"]["plan"]
        article = "an" if plan[0] in "aeiou" else "a"
        parts = [f"Triage: {ticket['priority']} {ticket['category'].replace('_', '-')} ticket from {article} {plan} customer."]
        if hit:
            parts.append(f"Relevant article: {hit['slug']} ({hit['title']}).")
        else:
            parts.append("No knowledge base article covers this; needs an engineer.")
        if ticket["assignee"] is None:
            parts.append("Unassigned, so next step is to assign an owner today.")
        return " ".join(parts)


def default_provider() -> Provider:
    return AnthropicProvider() if os.environ.get("ANTHROPIC_API_KEY") else DemoProvider()
