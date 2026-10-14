from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from helpdesk_mcp import HelpdeskStore
from helpdesk_mcp.seed import seed

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def store() -> Iterator[HelpdeskStore]:
    s = HelpdeskStore(":memory:", clock=lambda: NOW)
    seed(s, now=NOW)
    yield s
    s.close()
