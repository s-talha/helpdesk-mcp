"""The MCP server: helpdesk tools, resources and prompts.

Design rules:
- Tools take typed, length-limited arguments. The SDK turns the type hints into the JSON
  schema the model sees, and rejects bad input before our code runs.
- Expected failures (missing ticket, illegal status change) raise ToolError, so the model
  gets a clear message it can act on. Anything else is a crash and stays on the server.
- Write tools can be switched off entirely with read_only=True, and every write is audited.
- Customer email addresses are masked before they reach the model.
"""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .models import Category, Comment, Customer, KbHit, Priority, Stats, Status, Ticket, TicketSummary
from .store import HelpdeskStore, InvalidChangeError, NotFoundError

INSTRUCTIONS = """\
Tools for a customer support helpdesk.
Start with search_tickets or ticket_stats to find work, then get_ticket for full details.
Search the knowledge base with search_kb before writing to a customer.
Internal notes (add_comment with internal=true) are for agents only; customer-facing replies use internal=false.
Closed tickets are final. Never promise refunds or credits the knowledge base doesn't support.
"""

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

TicketId = Annotated[int, Field(ge=1, description="Ticket id, for example 3.")]
Query = Annotated[str, Field(max_length=200, description="Words to search for. Leave empty to match everything.")]


def build_server(store: HelpdeskStore, *, read_only: bool = False, actor: str = "mcp-agent") -> MCPServer:
    server = MCPServer(
        "helpdesk",
        title="Helpdesk",
        description="Search, read and update support tickets.",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    # ------------------------------------------------------------------ read tools

    @server.tool(annotations=READ)
    def search_tickets(
        query: Query = "",
        status: list[Status] | None = None,
        priority: list[Priority] | None = None,
        category: list[Category] | None = None,
        unassigned_only: bool = False,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> list[TicketSummary]:
        """Find tickets by text and filters. Results are sorted most urgent first, then oldest first."""
        return store.search_tickets(
            query,
            statuses=status or (),
            priorities=priority or (),
            categories=category or (),
            unassigned_only=unassigned_only,
            limit=limit,
        )

    @server.tool(annotations=READ)
    def get_ticket(ticket_id: TicketId) -> Ticket:
        """Get one ticket with its full text, the customer and all comments."""
        try:
            return store.get_ticket(ticket_id)
        except NotFoundError as e:
            raise ToolError(str(e)) from e

    @server.tool(annotations=READ)
    def get_customer(customer_id: Annotated[int, Field(ge=1)]) -> Customer:
        """Get a customer's company and plan. The email address is masked."""
        try:
            return store.get_customer(customer_id)
        except NotFoundError as e:
            raise ToolError(str(e)) from e

    @server.tool(annotations=READ)
    def search_kb(
        query: Annotated[str, Field(min_length=2, max_length=200)],
        category: Category | None = None,
    ) -> list[KbHit]:
        """Search knowledge base articles. Read the full article with the helpdesk://kb/{slug} resource."""
        return store.search_kb(query, category=category)

    @server.tool(annotations=READ)
    def ticket_stats() -> Stats:
        """Counts by status and priority, unassigned work, and the age of the oldest open ticket."""
        return store.stats()

    # ----------------------------------------------------------------- write tools

    if not read_only:

        @server.tool(annotations=WRITE)
        def add_comment(
            ticket_id: TicketId,
            body: Annotated[str, Field(min_length=1, max_length=4000)],
            internal: bool = True,
        ) -> Comment:
            """Add a comment. Internal notes (the default) are only visible to support staff."""
            try:
                return store.add_comment(ticket_id, body=body, internal=internal, actor=actor)
            except (NotFoundError, InvalidChangeError) as e:
                raise ToolError(str(e)) from e

        @server.tool(annotations=WRITE_IDEMPOTENT)
        def update_ticket(
            ticket_id: TicketId,
            status: Status | None = None,
            priority: Priority | None = None,
            assignee: Annotated[str | None, Field(max_length=40)] = None,
            unassign: bool = False,
        ) -> Ticket:
            """Change status, priority or assignee. Status changes follow the workflow:
            open <-> pending, open/pending -> resolved, resolved -> open or closed. Closed is final."""
            try:
                return store.update_ticket(
                    ticket_id, actor=actor, status=status, priority=priority, assignee=assignee, unassign=unassign
                )
            except (NotFoundError, InvalidChangeError) as e:
                raise ToolError(str(e)) from e

        @server.tool(annotations=WRITE)
        def create_ticket(
            customer_id: Annotated[int, Field(ge=1)],
            subject: Annotated[str, Field(min_length=3, max_length=200)],
            body: Annotated[str, Field(min_length=1, max_length=4000)],
            priority: Priority = Priority.NORMAL,
            category: Category = Category.HOW_TO,
        ) -> Ticket:
            """Open a new ticket for a customer."""
            try:
                return store.create_ticket(
                    customer_id=customer_id, subject=subject, body=body, priority=priority, category=category, actor=actor
                )
            except NotFoundError as e:
                raise ToolError(str(e)) from e

    # -------------------------------------------------------------------- resources

    @server.resource("helpdesk://tickets/{ticket_id}", mime_type="application/json", description="A ticket as JSON.")
    def ticket_resource(ticket_id: str) -> str:
        try:
            return store.get_ticket(int(ticket_id)).model_dump_json(indent=2)
        except (ValueError, NotFoundError) as e:
            raise ResourceNotFoundError(f"No ticket {ticket_id}.") from e

    @server.resource("helpdesk://kb", mime_type="text/markdown", description="Index of knowledge base articles.")
    def kb_index() -> str:
        lines = ["# Knowledge base", ""]
        lines += [f"- [{a.title}](helpdesk://kb/{a.slug}) ({a.category})" for a in store.list_kb()]
        return "\n".join(lines)

    @server.resource("helpdesk://kb/{slug}", mime_type="text/markdown", description="One knowledge base article.")
    def kb_article(slug: str) -> str:
        try:
            article = store.get_kb(slug)
        except NotFoundError as e:
            raise ResourceNotFoundError(str(e)) from e
        return f"# {article.title}\n\n{article.body}\n"

    @server.resource("helpdesk://audit", mime_type="application/json", description="The 50 most recent writes.")
    def audit() -> str:
        return json.dumps(store.audit_log(), indent=2, default=str)

    # ---------------------------------------------------------------------- prompts

    @server.prompt(title="Triage a ticket")
    def triage_ticket(ticket_id: str) -> str:
        """Classify a ticket, find the right article, and leave an internal note."""
        return (
            f"Triage helpdesk ticket {ticket_id}.\n"
            "1. Read it with get_ticket.\n"
            "2. Decide whether the priority and category are right. Urgent means work is blocked for many users or money is wrong.\n"
            "3. Search the knowledge base for the most relevant article.\n"
            "4. Add one internal note: your assessment, the article slug, and the suggested next step.\n"
            "5. Change the priority only if it's clearly wrong. Don't reply to the customer."
        )

    @server.prompt(title="Draft a reply")
    def draft_reply(ticket_id: str, tone: str = "friendly and concise") -> str:
        """Draft a customer reply grounded in the knowledge base. The draft is not sent."""
        return (
            f"Draft a reply to the customer on ticket {ticket_id}, in a {tone} tone.\n"
            "Read the ticket and search the knowledge base first. Only promise what an article supports, "
            "and say so when you're unsure. Return the draft as text; don't post it."
        )

    return server
