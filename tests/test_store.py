from __future__ import annotations

import pytest

from helpdesk_mcp.models import Priority, Status, mask_email
from helpdesk_mcp.store import HelpdeskStore, InvalidChangeError, NotFoundError


def test_search_orders_most_urgent_then_oldest(store: HelpdeskStore) -> None:
    results = store.search_tickets(statuses=[Status.OPEN])
    ranks = [list(Priority).index(t.priority) for t in results]
    assert ranks == sorted(ranks, reverse=True)  # urgent (last in the enum) first
    urgent = [t for t in results if t.priority == Priority.URGENT]
    assert [t.created_at for t in urgent] == sorted(t.created_at for t in urgent)


def test_search_filters_combine(store: HelpdeskStore) -> None:
    results = store.search_tickets(statuses=[Status.OPEN], priorities=[Priority.URGENT], unassigned_only=True)
    assert {t.id for t in results} == {1, 2}


def test_text_search_matches_word_forms(store: HelpdeskStore) -> None:
    assert [t.id for t in store.search_tickets("charges")] == [2]


def test_customer_email_is_masked(store: HelpdeskStore) -> None:
    assert store.get_customer(1).email == "a***@nordvik.example"
    assert store.get_ticket(2).customer.email == "a***@nordvik.example"
    assert mask_email("not-an-email") == "***"


def test_missing_records_raise_not_found(store: HelpdeskStore) -> None:
    with pytest.raises(NotFoundError, match="No ticket with id 999"):
        store.get_ticket(999)
    with pytest.raises(NotFoundError):
        store.get_kb("nope")


@pytest.mark.parametrize(
    ("ticket_id", "target"),
    [(16, Status.OPEN), (16, Status.RESOLVED), (1, Status.CLOSED), (12, Status.PENDING)],
)
def test_illegal_status_changes_are_refused(store: HelpdeskStore, ticket_id: int, target: Status) -> None:
    with pytest.raises(InvalidChangeError, match="Allowed next statuses"):
        store.update_ticket(ticket_id, actor="test", status=target)


def test_update_is_idempotent_and_audited_once(store: HelpdeskStore) -> None:
    store.update_ticket(2, actor="test", assignee="nora", priority=Priority.HIGH)
    store.update_ticket(2, actor="test", assignee="nora", priority=Priority.HIGH)
    log = store.audit_log()
    assert len(log) == 1
    assert log[0]["details"] == {"priority": "high", "assignee": "nora"}


def test_comments_on_closed_tickets_are_refused(store: HelpdeskStore) -> None:
    with pytest.raises(InvalidChangeError, match="closed"):
        store.add_comment(16, body="hello", internal=True, actor="test")


def test_create_ticket_validates_customer(store: HelpdeskStore) -> None:
    with pytest.raises(NotFoundError):
        store.create_ticket(customer_id=999, subject="x" * 5, body="y", priority=Priority.LOW, category="bug", actor="t")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("query", "slug"),
    [
        ("Charged twice for October", "refund-policy"),
        ("Teachers locked out after SSO change", "sso-setup"),
        ("API returns 500 on bulk upload", "api-limits"),
        ("Card declined when upgrading", "card-declined"),
        ("Webhook retries flooding our endpoint", "webhooks"),
        ("Two-factor codes arriving late", "two-factor"),
    ],
)
def test_kb_search_finds_the_right_article(store: HelpdeskStore, query: str, slug: str) -> None:
    assert store.search_kb(query)[0].slug == slug


def test_stats(store: HelpdeskStore) -> None:
    stats = store.stats()
    assert stats.by_status == {"open": 11, "pending": 2, "resolved": 2, "closed": 1}
    assert stats.by_priority_open["urgent"] == 3
    assert stats.oldest_open_hours == 96.0
