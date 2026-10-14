"""Talks to the server the way Claude Desktop does: a subprocess over stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from helpdesk_mcp.agent import DemoProvider, run_agent

pytestmark = pytest.mark.anyio

SRC = str(Path(__file__).resolve().parents[1] / "src")


def params(db: Path, *extra: str) -> StdioServerParameters:
    env = {**os.environ, "PYTHONPATH": SRC + os.pathsep + os.environ.get("PYTHONPATH", "")}
    return StdioServerParameters(command=sys.executable, args=["-m", "helpdesk_mcp", "--db", str(db), *extra], env=env)


async def test_server_over_stdio(tmp_path: Path) -> None:
    async with Client(params(tmp_path / "h.db")) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        result = await run_agent(client, "Triage ticket 3", DemoProvider())
    assert "update_ticket" in names
    assert "sso-setup" in result.output


async def test_data_persists_between_server_runs(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    async with Client(params(db)) as client:
        await client.call_tool("add_comment", {"ticket_id": 5, "body": "Persist me"})
    async with Client(params(db, "--read-only")) as client:
        ticket = await client.call_tool("get_ticket", {"ticket_id": 5})
        names = {t.name for t in (await client.list_tools()).tools}
    assert any(c["body"] == "Persist me" for c in ticket.structured_content["comments"])
    assert "add_comment" not in names
