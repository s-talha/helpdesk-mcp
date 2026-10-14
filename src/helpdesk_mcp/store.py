"""SQLite-backed helpdesk store.

All SQL lives here. The MCP server never builds queries from model input: it calls these
methods with validated, typed arguments, and every query is parameterised.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import (
    TRANSITIONS,
    Category,
    Comment,
    Customer,
    KbArticle,
    KbHit,
    Plan,
    Priority,
    Stats,
    Status,
    Ticket,
    TicketSummary,
    mask_email,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    company TEXT NOT NULL,
    plan TEXT NOT NULL,
    email TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    category TEXT NOT NULL,
    assignee TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tickets_status ON tickets(status, priority);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    internal INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_articles (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    ticket_id INTEGER,
    details TEXT NOT NULL
);
"""

PRIORITY_RANK = {Priority.URGENT: 0, Priority.HIGH: 1, Priority.NORMAL: 2, Priority.LOW: 3}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    """a about after all also an and any are as at be been but by can could did do does for from get got had has have
    how i if in into is it its just me my no not of on only or our out over so still than that the their then there
    this to too up us use was we were what when where which why will with would you your""".split()
)


class NotFoundError(LookupError):
    """The requested ticket, customer or article does not exist."""


class InvalidChangeError(ValueError):
    """The requested change breaks a business rule."""


def _stem(word: str) -> str:
    """Light suffix stripping so 'charged', 'charges' and 'charge' match. Enough for search, not linguistics."""
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in _WORD.findall(text.lower()) if t not in _STOP]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


class HelpdeskStore:
    def __init__(self, path: str | Path = ":memory:", *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        # The MCP server may call in from worker threads; one lock keeps writes ordered.
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def now(self) -> datetime:
        return self._clock()

    def is_empty(self) -> bool:
        return self._db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0

    # ------------------------------------------------------------------ reads

    def get_customer(self, customer_id: int) -> Customer:
        row = self._db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No customer with id {customer_id}.")
        return Customer(
            id=row["id"], name=row["name"], company=row["company"], plan=Plan(row["plan"]), email=mask_email(row["email"])
        )

    def get_ticket(self, ticket_id: int) -> Ticket:
        row = self._db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No ticket with id {ticket_id}.")
        comments = [
            Comment(
                id=c["id"],
                author=c["author"],
                body=c["body"],
                internal=bool(c["internal"]),
                created_at=datetime.fromisoformat(c["created_at"]),
            )
            for c in self._db.execute("SELECT * FROM comments WHERE ticket_id = ? ORDER BY id", (ticket_id,))
        ]
        return Ticket(
            **self._summary(row).model_dump(),
            body=row["body"],
            customer=self.get_customer(row["customer_id"]),
            comments=comments,
        )

    def search_tickets(
        self,
        query: str = "",
        *,
        statuses: Sequence[Status] = (),
        priorities: Sequence[Priority] = (),
        categories: Sequence[Category] = (),
        customer_id: int | None = None,
        unassigned_only: bool = False,
        limit: int = 20,
    ) -> list[TicketSummary]:
        where: list[str] = []
        params: list[Any] = []
        for column, values in (("status", statuses), ("priority", priorities), ("category", categories)):
            if values:
                where.append(f"{column} IN ({','.join('?' * len(values))})")
                params.extend(str(v) for v in values)
        if customer_id is not None:
            where.append("customer_id = ?")
            params.append(customer_id)
        if unassigned_only:
            where.append("assignee IS NULL")
        for token in _tokens(query)[:8]:
            where.append("(lower(subject) LIKE ? OR lower(body) LIKE ?)")
            params.extend([f"%{token}%", f"%{token}%"])
        sql = "SELECT * FROM tickets" + (" WHERE " + " AND ".join(where) if where else "")
        rows = [self._summary(r) for r in self._db.execute(sql, params)]
        # Most urgent first, then oldest first: the order a support lead would work through them.
        rows.sort(key=lambda t: (PRIORITY_RANK[t.priority], t.created_at))
        return rows[:limit]

    def stats(self) -> Stats:
        by_status = Counter(r[0] for r in self._db.execute("SELECT status FROM tickets"))
        active = [Status.OPEN, Status.PENDING]
        rows = self._db.execute(
            "SELECT priority, assignee, created_at FROM tickets WHERE status IN (?, ?)", [str(s) for s in active]
        ).fetchall()
        oldest = min((datetime.fromisoformat(r["created_at"]) for r in rows), default=None)
        return Stats(
            by_status={s.value: by_status.get(s.value, 0) for s in Status},
            by_priority_open={p.value: sum(1 for r in rows if r["priority"] == p.value) for p in Priority},
            unassigned_open=sum(1 for r in rows if r["assignee"] is None),
            oldest_open_hours=round((self.now() - oldest).total_seconds() / 3600, 1) if oldest else None,
        )

    def list_kb(self) -> list[KbArticle]:
        return [self._article(r) for r in self._db.execute("SELECT * FROM kb_articles ORDER BY title")]

    def get_kb(self, slug: str) -> KbArticle:
        row = self._db.execute("SELECT * FROM kb_articles WHERE slug = ?", (slug,)).fetchone()
        if row is None:
            raise NotFoundError(f"No knowledge base article '{slug}'.")
        return self._article(row)

    def search_kb(self, query: str, *, category: Category | None = None, limit: int = 3) -> list[KbHit]:
        """Small BM25 ranking over title and body. Plenty for a few dozen articles, and no extra service."""
        articles = [a for a in self.list_kb() if category is None or a.category == category]
        q = set(_tokens(query))
        if not q or not articles:
            return []
        docs = [_tokens(f"{a.title} {a.title} {a.body}") for a in articles]
        avg_len = sum(map(len, docs)) / len(docs)
        df = Counter(t for d in docs for t in set(d))
        hits: list[KbHit] = []
        for article, doc in zip(articles, docs, strict=True):
            tf = Counter(doc)
            score = 0.0
            for t in q & tf.keys():
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * 2.2 / (tf[t] + 1.2 * (0.25 + 0.75 * len(doc) / avg_len))
            if score > 0:
                snippet = article.body.split("\n\n")[0][:220]
                hits.append(KbHit(slug=article.slug, title=article.title, category=article.category, snippet=snippet, score=round(score, 3)))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]

    def audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "details": json.loads(r["details"])} for r in rows]

    # ----------------------------------------------------------------- writes

    def create_ticket(
        self,
        *,
        customer_id: int,
        subject: str,
        body: str,
        priority: Priority,
        category: Category,
        actor: str,
    ) -> Ticket:
        self.get_customer(customer_id)  # raises NotFoundError
        now = _iso(self.now())
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO tickets (customer_id, subject, body, status, priority, category, assignee, created_at, updated_at)"
                " VALUES (?, ?, ?, 'open', ?, ?, NULL, ?, ?)",
                (customer_id, subject, body, str(priority), str(category), now, now),
            )
            ticket_id = int(cur.lastrowid or 0)
            self._audit(actor, "create_ticket", ticket_id, {"subject": subject, "priority": priority, "category": category})
        return self.get_ticket(ticket_id)

    def add_comment(self, ticket_id: int, *, body: str, internal: bool, actor: str) -> Comment:
        ticket = self.get_ticket(ticket_id)
        if ticket.status == Status.CLOSED:
            raise InvalidChangeError(f"Ticket {ticket_id} is closed. Open a new ticket instead.")
        now = _iso(self.now())
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO comments (ticket_id, author, body, internal, created_at) VALUES (?, ?, ?, ?, ?)",
                (ticket_id, actor, body, int(internal), now),
            )
            self._db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
            self._audit(actor, "add_comment", ticket_id, {"internal": internal, "length": len(body)})
        return Comment(id=int(cur.lastrowid or 0), author=actor, body=body, internal=internal, created_at=datetime.fromisoformat(now))

    def update_ticket(
        self,
        ticket_id: int,
        *,
        actor: str,
        status: Status | None = None,
        priority: Priority | None = None,
        assignee: str | None = None,
        unassign: bool = False,
    ) -> Ticket:
        ticket = self.get_ticket(ticket_id)
        changes: dict[str, Any] = {}
        if status is not None and status != ticket.status:
            if status not in TRANSITIONS[ticket.status]:
                allowed = ", ".join(sorted(TRANSITIONS[ticket.status])) or "none, it is final"
                raise InvalidChangeError(
                    f"Can't move ticket {ticket_id} from {ticket.status} to {status}. Allowed next statuses: {allowed}."
                )
            changes["status"] = str(status)
        if priority is not None and priority != ticket.priority:
            changes["priority"] = str(priority)
        if unassign and ticket.assignee is not None:
            changes["assignee"] = None
        elif assignee is not None and assignee != ticket.assignee:
            changes["assignee"] = assignee
        if not changes:
            return ticket  # idempotent: repeating a change is harmless
        changes["updated_at"] = _iso(self.now())
        sets = ", ".join(f"{k} = ?" for k in changes)
        with self._lock, self._db:
            self._db.execute(f"UPDATE tickets SET {sets} WHERE id = ?", [*changes.values(), ticket_id])  # noqa: S608 keys are fixed column names
            self._audit(actor, "update_ticket", ticket_id, {k: v for k, v in changes.items() if k != "updated_at"})
        return self.get_ticket(ticket_id)

    # ---------------------------------------------------------------- seeding

    def insert_seed(
        self,
        customers: Iterable[tuple[Any, ...]],
        tickets: Iterable[tuple[Any, ...]],
        comments: Iterable[tuple[Any, ...]],
        articles: Iterable[tuple[Any, ...]],
    ) -> None:
        with self._lock, self._db:
            self._db.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?)", customers)
            self._db.executemany("INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tickets)
            self._db.executemany(
                "INSERT INTO comments (ticket_id, author, body, internal, created_at) VALUES (?, ?, ?, ?, ?)", comments
            )
            self._db.executemany("INSERT INTO kb_articles VALUES (?, ?, ?, ?)", articles)

    # ---------------------------------------------------------------- helpers

    def _audit(self, actor: str, action: str, ticket_id: int | None, details: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT INTO audit_log (at, actor, action, ticket_id, details) VALUES (?, ?, ?, ?, ?)",
            (_iso(self.now()), actor, action, ticket_id, json.dumps(details, default=str)),
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> TicketSummary:
        return TicketSummary(
            id=row["id"],
            subject=row["subject"],
            status=Status(row["status"]),
            priority=Priority(row["priority"]),
            category=Category(row["category"]),
            customer_id=row["customer_id"],
            assignee=row["assignee"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _article(row: sqlite3.Row) -> KbArticle:
        return KbArticle(slug=row["slug"], title=row["title"], category=Category(row["category"]), body=row["body"])
