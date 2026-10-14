"""Domain models shared by the store, the MCP server and the agent."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Category(StrEnum):
    BILLING = "billing"
    BUG = "bug"
    ACCOUNT = "account"
    HOW_TO = "how_to"


class Plan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# Allowed status changes. Closed is final: a closed ticket gets a new ticket, not a reopen,
# so reporting on resolution times stays honest.
TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.OPEN: frozenset({Status.PENDING, Status.RESOLVED}),
    Status.PENDING: frozenset({Status.OPEN, Status.RESOLVED}),
    Status.RESOLVED: frozenset({Status.OPEN, Status.CLOSED}),
    Status.CLOSED: frozenset(),
}


class Customer(BaseModel):
    id: int
    name: str
    company: str
    plan: Plan
    email: str = Field(description="Masked: the local part is hidden to keep personal data out of model context.")


class Comment(BaseModel):
    id: int
    author: str
    body: str
    internal: bool = Field(description="Internal notes are visible to agents only, never to the customer.")
    created_at: datetime


class TicketSummary(BaseModel):
    id: int
    subject: str
    status: Status
    priority: Priority
    category: Category
    customer_id: int
    assignee: str | None
    created_at: datetime
    updated_at: datetime


class Ticket(TicketSummary):
    body: str
    customer: Customer
    comments: list[Comment]


class KbArticle(BaseModel):
    slug: str
    title: str
    category: Category
    body: str


class KbHit(BaseModel):
    slug: str
    title: str
    category: Category
    snippet: str
    score: float


class Stats(BaseModel):
    by_status: dict[str, int]
    by_priority_open: dict[str, int] = Field(description="Open and pending tickets by priority.")
    unassigned_open: int
    oldest_open_hours: float | None


def mask_email(email: str) -> str:
    """a.person@acme.se -> a***@acme.se"""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
