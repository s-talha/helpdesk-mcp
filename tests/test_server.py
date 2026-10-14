from __future__ import annotations

import json

import pytest
from mcp import Client

from helpdesk_mcp import HelpdeskStore, build_server

pytestmark = pytest.mark.anyio

WRITE_TOOLS = {"add_comment", "update_ticket", "create_ticket"}


async def test_tools_are_listed_with_safety_annotations(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"search_tickets", "get_ticket", "get_customer", "search_kb", "ticket_stats", *WRITE_TOOLS}
    for name, tool in tools.items():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is (name not in WRITE_TOOLS)
        assert tool.description, f"{name} needs a description the model can read"


async def test_read_only_mode_hides_write_tools(store: HelpdeskStore) -> None:
    async with Client(build_server(store, read_only=True)) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert not names & WRITE_TOOLS


async def test_structured_results(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        found = await client.call_tool("search_tickets", {"status": ["open"], "priority": ["urgent"]})
        ticket = await client.call_tool("get_ticket", {"ticket_id": 2})
    assert [t["id"] for t in found.structured_content["result"]] == [2, 1, 3]
    assert ticket.structured_content["customer"]["email"] == "a***@nordvik.example"


async def test_expected_failures_reach_the_model(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        missing = await client.call_tool("get_ticket", {"ticket_id": 999})
        illegal = await client.call_tool("update_ticket", {"ticket_id": 16, "status": "open"})
    assert missing.is_error and "No ticket with id 999" in missing.content[0].text
    assert illegal.is_error and "Allowed next statuses" in illegal.content[0].text


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_ticket", {"ticket_id": 0}),
        ("get_ticket", {"ticket_id": "abc"}),
        ("search_tickets", {"limit": 500}),
        ("search_tickets", {"status": ["deleted"]}),
        ("add_comment", {"ticket_id": 1, "body": ""}),
    ],
)
async def test_invalid_arguments_are_rejected(store: HelpdeskStore, tool: str, args: dict) -> None:
    async with Client(build_server(store)) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert store.audit_log() == []


async def test_writes_are_audited(store: HelpdeskStore) -> None:
    async with Client(build_server(store, actor="tester")) as client:
        await client.call_tool("add_comment", {"ticket_id": 1, "body": "Looking into it."})
        audit = await client.read_resource("helpdesk://audit")
    entries = json.loads(audit.contents[0].text)
    assert entries[0]["actor"] == "tester" and entries[0]["action"] == "add_comment"


async def test_resources(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        ticket = await client.read_resource("helpdesk://tickets/3")
        index = await client.read_resource("helpdesk://kb")
        article = await client.read_resource("helpdesk://kb/sso-setup")
        with pytest.raises(Exception, match="No knowledge base article"):
            await client.read_resource("helpdesk://kb/nope")
    assert json.loads(ticket.contents[0].text)["subject"].startswith("Teachers locked out")
    assert "helpdesk://kb/refund-policy" in index.contents[0].text
    assert "invalid audience" in article.contents[0].text


async def test_prompts(store: HelpdeskStore) -> None:
    async with Client(build_server(store)) as client:
        names = {p.name for p in (await client.list_prompts()).prompts}
        triage = await client.get_prompt("triage_ticket", {"ticket_id": "3"})
    assert names == {"triage_ticket", "draft_reply"}
    assert "ticket 3" in triage.messages[0].content.text
