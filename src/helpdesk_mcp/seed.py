"""Deterministic demo data: the same tickets, customers and articles on every run."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from .store import HelpdeskStore

CUSTOMERS = [
    (1, "Astrid Lind", "Nordvik Logistics", "enterprise", "astrid.lind@nordvik.example"),
    (2, "Jonas Berg", "Fjellstad Bakery", "free", "jonas@fjellstad.example"),
    (3, "Maja Ek", "Ekholm Dental", "pro", "maja.ek@ekholm.example"),
    (4, "Omar Haddad", "Harbor Freight Co", "enterprise", "omar.haddad@harborfreight.example"),
    (5, "Elin Sund", "Sundbyberg Yoga", "free", "elin@sundyoga.example"),
    (6, "Lukas Holm", "Holm & Partners Law", "pro", "lukas.holm@holmlaw.example"),
    (7, "Sara Nilsson", "Pinewood Schools", "enterprise", "s.nilsson@pinewood.example"),
    (8, "Ahmed Rana", "Rana Imports", "pro", "ahmed@ranaimports.example"),
    (9, "Freja Dahl", "Dahl Design Studio", "free", "freja@dahldesign.example"),
    (10, "Viktor Ström", "Strom Energy", "enterprise", "viktor.strom@stromenergy.example"),
]

# (customer, subject, body, status, priority, category, assignee, hours_ago)
TICKETS = [
    (4, "Invoices export is empty since the update", "Since yesterday's release the invoice export downloads a CSV with only headers. Our finance team closes the month on Friday.", "open", "urgent", "bug", None, 6),
    (1, "Charged twice for October", "We were charged twice for the October enterprise invoice. Please refund the duplicate payment.", "open", "urgent", "billing", None, 9),
    (7, "Teachers locked out after SSO change", "After switching our SSO provider none of our 140 teachers can sign in. Error says 'invalid audience'.", "open", "urgent", "account", "nora", 3),
    (10, "API returns 500 on bulk upload", "POST /v2/readings/bulk fails with a 500 for batches over 1,000 rows. Smaller batches work.", "open", "high", "bug", None, 20),
    (3, "How do I add a second clinic location?", "We opened a second clinic. How do we add it so bookings are separate?", "open", "normal", "how_to", None, 30),
    (6, "Need a copy of our data processing agreement", "Our compliance officer needs the signed DPA for our files.", "pending", "normal", "account", "erik", 50),
    (2, "Card declined when upgrading", "I tried to upgrade to Pro and my card was declined, but my bank says it's fine.", "open", "high", "billing", None, 14),
    (8, "Reports show wrong currency", "Our monthly report shows SEK but our account is set to EUR.", "open", "normal", "bug", None, 70),
    (5, "Can I pause my subscription in summer?", "We close for July. Is there a way to pause and not pay?", "open", "low", "billing", None, 96),
    (9, "Change account owner", "I'm leaving the studio. How do I transfer the account to my colleague?", "open", "normal", "account", None, 40),
    (4, "Webhook retries flooding our endpoint", "Your webhook retried the same event 300 times in an hour. Is there a backoff?", "pending", "high", "bug", "nora", 28),
    (1, "Add 25 more seats", "Please add 25 seats to our plan from next month.", "resolved", "normal", "billing", "erik", 200),
    (7, "Export student attendance", "How do I export attendance for one class for the whole term?", "resolved", "low", "how_to", "erik", 300),
    (10, "Two-factor codes arriving late", "SMS codes arrive after they expire for some of our engineers.", "open", "high", "account", None, 18),
    (3, "Booking page shows old opening hours", "We changed our opening hours but the public booking page still shows the old ones.", "open", "normal", "bug", None, 55),
    (6, "Invoice needs our VAT number", "Our invoices are missing our VAT number. Can you reissue them?", "closed", "low", "billing", "erik", 600),
]

ARTICLES = [
    ("refund-policy", "Refunds and duplicate charges", "billing",
     "Duplicate charges are refunded in full within 5 business days. Refunds for unused time are available within 30 days of payment.\n\nTo request one, open a billing ticket with the invoice number. Enterprise customers can also ask their account manager."),
    ("pause-subscription", "Pausing a subscription", "billing",
     "Pro and Enterprise plans can be paused for up to 3 months per year from Settings, Billing, Pause plan. Data is kept while paused.\n\nFree plans don't need pausing."),
    ("card-declined", "Why was my card declined?", "billing",
     "Most declines come from 3-D Secure checks or a billing address that doesn't match the card. Try again and complete the bank's verification step.\n\nIf it still fails, pay by invoice from Settings, Billing."),
    ("sso-setup", "Setting up single sign-on (SSO)", "account",
     "SSO uses SAML 2.0. The audience URI must be exactly https://app.example.com/saml/metadata. An 'invalid audience' error means the identity provider is sending a different value.\n\nAdmins can sign in with a password at /login?sso=off to fix the setting."),
    ("transfer-ownership", "Transferring account ownership", "account",
     "The current owner can transfer ownership from Settings, Team, Owner. The new owner must already be a team member and confirm by email."),
    ("two-factor", "Two-factor authentication problems", "account",
     "SMS codes are valid for 10 minutes. Delays usually come from the mobile carrier. We recommend switching to an authenticator app from Settings, Security."),
    ("export-data", "Exporting invoices, reports and attendance", "how_to",
     "Every list view has an Export button that downloads a CSV of the current filters. Large exports are emailed when ready.\n\nIf an export is empty, check that the date filter covers the period you need."),
    ("multiple-locations", "Managing multiple locations", "how_to",
     "Add a location from Settings, Locations, Add location. Each location gets its own booking page, opening hours and staff list."),
    ("webhooks", "Webhooks and retries", "bug",
     "Failed webhook deliveries are retried with exponential backoff for up to 24 hours. Return a 2xx status quickly to acknowledge an event.\n\nRepeated identical retries usually mean the endpoint is timing out after more than 10 seconds."),
    ("api-limits", "API limits and bulk uploads", "bug",
     "Bulk endpoints accept up to 500 rows per request. Larger batches should be split. Requests over the limit currently fail with a server error, which is a known issue."),
]

COMMENTS = [
    (3, "nora", "Asked the customer for their IdP metadata file.", 1, 2),
    (6, "erik", "DPA sent for signature, waiting for the customer.", 0, 40),
    (11, "nora", "Engineering confirmed retries ignore backoff for 410 responses. Fix scheduled.", 1, 20),
]


def seed(store: HelpdeskStore, *, now: datetime | None = None) -> None:
    """Fill an empty store with demo data. Ticket ages are relative to `now`."""
    if not store.is_empty():
        return
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    rng = random.Random(42)
    tickets = []
    for i, (customer, subject, body, status, priority, category, assignee, hours) in enumerate(TICKETS, start=1):
        created = now - timedelta(hours=hours)
        updated = created + timedelta(hours=rng.uniform(0, min(hours, 6)))
        tickets.append((i, customer, subject, body, status, priority, category, assignee, created.isoformat(), updated.isoformat()))
    comments = [(t, a, b, internal, (now - timedelta(hours=h)).isoformat()) for t, a, b, internal, h in COMMENTS]
    store.insert_seed(CUSTOMERS, tickets, comments, ARTICLES)
