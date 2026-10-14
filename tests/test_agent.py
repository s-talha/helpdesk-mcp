from __future__ import annotations

from typing import Any

import pytest
from mcp import Client

from helpdesk_mcp import HelpdeskStore, build_server
from helpdesk_mcp.agent import DemoProvider, run_agent
from helpdesk_mcp.agent.providers import ModelTurn

pytestmark = pytest.mark.anyio


async def test_demo_agent_triages_urgent_tickets_end_to_end(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        result = await run_agent(client, "Triage the urgent open tickets", DemoProvider())

    assert result.ok
    calls = [e.data["name"] for e in result.events if e.kind == "tool_call"]
    assert calls[0] == "search_tickets"
    assert calls.count("get_ticket") == 3 and calls.count("add_comment") == 3

    notes = {t: [c.body for c in store.get_ticket(t).comments if c.author == "mcp-agent"] for t in (1, 2, 3)}
    assert "refund-policy" in notes[2][0]
    assert "sso-setup" in notes[3][0]
    assert all(c.internal for t in (1, 2, 3) for c in store.get_ticket(t).comments)
    assert "#2 Charged twice for October" in result.output


async def test_read_only_server_means_no_writes(store: HelpdeskStore) -> None:
    async with Client(build_server(store, read_only=True)) as client:
        result = await run_agent(client, "Triage ticket 4", DemoProvider())
    assert "api-limits" in result.output
    assert "no notes were written" in result.output
    assert store.audit_log() == []


async def test_declined_writes_are_not_executed(store: HelpdeskStore) -> None:
    asked: list[str] = []

    async def decline(name: str, _args: dict[str, Any]) -> bool:
        asked.append(name)
        return False

    async with Client(build_server(store)) as client:
        result = await run_agent(client, "Triage ticket 4", DemoProvider(), approve_writes=decline)

    assert asked == ["add_comment"]
    assert [e.kind for e in result.events].count("denied") == 1
    assert "declined" in result.output
    assert store.audit_log() == []


async def test_reads_never_ask_for_approval(store: HelpdeskStore) -> None:
    async def fail(name: str, _args: dict[str, Any]) -> bool:
        raise AssertionError(f"asked to approve read tool {name}")

    async with Client(build_server(store, read_only=True)) as client:
        await run_agent(client, "Triage the urgent open tickets", DemoProvider(), approve_writes=fail)


async def test_step_limit_stops_a_looping_model(store: HelpdeskStore) -> None:
    class Looper:
        model = "looper"

        async def complete(self, **_: Any) -> ModelTurn:
            call = {"type": "tool_use", "id": "t", "name": "ticket_stats", "input": {}}
            return ModelTurn([call], "tool_use")

    async with Client(build_server(store)) as client:
        result = await run_agent(client, "anything", Looper(), max_steps=3)
    assert not result.ok and result.steps == 3
    assert "Stopped after 3 steps" in result.output


async def test_tool_errors_are_passed_back_to_the_model(store: HelpdeskStore) -> None:
    seen: list[Any] = []

    class Scripted:
        model = "scripted"

        async def complete(self, *, messages: list[dict[str, Any]], **_: Any) -> ModelTurn:
            if len(messages) == 1:
                return ModelTurn([{"type": "tool_use", "id": "a", "name": "get_ticket", "input": {"ticket_id": 999}}], "tool_use")
            seen.append(messages[-1]["content"][0])
            return ModelTurn([{"type": "text", "text": "That ticket doesn't exist."}], "end_turn")

    async with Client(build_server(store)) as client:
        result = await run_agent(client, "Look at ticket 999", Scripted())
    assert seen[0]["is_error"] is True and "No ticket with id 999" in seen[0]["content"]
    assert result.output == "That ticket doesn't exist."


async def test_demo_model_declines_unknown_tasks(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        result = await run_agent(client, "Write a poem", DemoProvider())
    assert "can only triage" in result.output
