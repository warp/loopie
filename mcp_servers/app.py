"""
MCP server: Google Calendar and Google Tasks (when creds are set), plus in-memory external notes.

Run SSE:  python -m mcp_servers.app sse   (default)
Run stdio: python -m mcp_servers.app stdio

SSE URL for ADK: http://127.0.0.1:8765/sse (set MCP_SSE_URL)
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# So shared Google API env vars and paths in .env apply when running: python -m mcp_servers.app sse
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers import calendar_google
from mcp_servers import people_google
from mcp_servers import tasks_google

_notes: list[dict[str, Any]] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_server() -> FastMCP:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", os.environ.get("PORT", "8765")))
    mcp = FastMCP(
        "loopie-mcp",
        host=host,
        port=port,
    )

    @mcp.tool()
    def calendar_create_event(title: str, start_iso: str, end_iso: str | None = None) -> str:
        """Create a Google Calendar event. start_iso is ISO-8601, or date-only YYYY-MM-DD to pick the earliest free slot within BUSINESS_HOURS (env) using free/busy. Omit end_iso for default duration or for date-only mode."""
        return calendar_google.create_event(title, start_iso, end_iso)

    @mcp.tool()
    def calendar_list_events(start_iso: str, end_iso: str) -> str:
        """List Google Calendar events in the time window. start_iso and end_iso must be ISO-8601."""
        return calendar_google.list_events(start_iso, end_iso)

    @mcp.tool()
    def calendar_update_event(
        event_id: str,
        title: str | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> str:
        """Update an existing calendar event by event_id (from calendar_list_events). Omit optional fields to leave them unchanged. start_iso/end_iso are ISO-8601; timed events only."""
        return calendar_google.update_event(
            event_id,
            title=title,
            start_iso=start_iso,
            end_iso=end_iso,
            location=location,
            description=description,
        )

    @mcp.tool()
    def calendar_invite_to_event(event_id: str, attendee_emails: str) -> str:
        """Add invitees to an existing event. event_id from calendar_list_events. attendee_emails: comma-separated email addresses; sends calendar invitations."""
        return calendar_google.invite_to_event(event_id, attendee_emails)

    @mcp.tool()
    def external_task_create(title: str, due_iso: str | None = None) -> str:
        """Create a task in Google Tasks. due_iso is optional RFC3339."""
        return tasks_google.create_task(title, due_iso)

    @mcp.tool()
    def external_task_list() -> str:
        """List tasks from Google Tasks (current list; see GOOGLE_TASKS_LIST_ID)."""
        return tasks_google.list_tasks()

    @mcp.tool()
    def external_task_complete(task_id: str) -> str:
        """Mark a Google Tasks task completed by task_id."""
        return tasks_google.complete_task(task_id)

    @mcp.tool()
    def external_contact_search(query: str, limit: int = 10) -> str:
        """Search the user's Google Contacts (My Contacts): matches name, email, or phone substring. Returns display_name, emails, phones, primary_email."""
        return people_google.search_contacts(query, limit)

    @mcp.tool()
    def external_note_create(title: str, body: str) -> str:
        """Create a note in the external notes integration (in-memory)."""
        nid = str(uuid.uuid4())
        note = {"note_id": nid, "title": title, "body": body, "created_at": _now_iso()}
        _notes.append(note)
        return json.dumps(note, indent=2)

    @mcp.tool()
    def external_note_search(query: str) -> str:
        """Search external notes by substring in title or body (in-memory)."""
        q = query.lower()
        hits = [n for n in _notes if q in n["title"].lower() or q in n["body"].lower()]
        return json.dumps(hits, indent=2)

    return mcp


def main() -> None:
    mode = "sse"
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    mcp = build_server()
    if mode == "stdio":
        asyncio.run(mcp.run_stdio_async())
    else:
        asyncio.run(mcp.run_sse_async())


if __name__ == "__main__":
    main()
