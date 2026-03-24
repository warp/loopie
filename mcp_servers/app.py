"""
Stub MCP server: calendar, external task list, and external notes.
Run SSE:  python -m mcp_servers.app sse   (default)
Run stdio: python -m mcp_servers.app stdio

SSE URL for ADK: http://127.0.0.1:8765/sse (set MCP_SSE_URL)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

_calendar: list[dict[str, Any]] = []
_tasks: dict[str, dict[str, Any]] = {}
_notes: list[dict[str, Any]] = []


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_server() -> FastMCP:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", os.environ.get("PORT", "8765")))
    mcp = FastMCP(
        "personal-agent-stub-mcp",
        host=host,
        port=port,
    )

    @mcp.tool()
    def calendar_create_event(title: str, start_iso: str, end_iso: str) -> str:
        """Create a calendar event (stub). start_iso and end_iso must be ISO-8601."""
        eid = str(uuid.uuid4())
        ev = {
            "event_id": eid,
            "title": title,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "created_at": _now_iso(),
        }
        _calendar.append(ev)
        return json.dumps(ev, indent=2)

    @mcp.tool()
    def calendar_list_events(start_iso: str, end_iso: str) -> str:
        """List events overlapping the given window (stub; simple string compare)."""
        out = [e for e in _calendar if e["start_iso"] <= end_iso and e["end_iso"] >= start_iso]
        return json.dumps(out, indent=2)

    @mcp.tool()
    def external_task_create(title: str, due_iso: str | None = None) -> str:
        """Create a task in the external task manager integration (stub, not AlloyDB)."""
        tid = str(uuid.uuid4())
        item = {"task_id": tid, "title": title, "due_iso": due_iso, "status": "open"}
        _tasks[tid] = item
        return json.dumps(item, indent=2)

    @mcp.tool()
    def external_task_list() -> str:
        """List tasks from the external task manager (stub)."""
        return json.dumps(list(_tasks.values()), indent=2)

    @mcp.tool()
    def external_task_complete(task_id: str) -> str:
        """Mark an external task completed (stub)."""
        t = _tasks.get(task_id)
        if not t:
            return json.dumps({"error": "unknown task_id"})
        t["status"] = "done"
        return json.dumps(t, indent=2)

    @mcp.tool()
    def external_note_create(title: str, body: str) -> str:
        """Create a note in the external notes integration (stub)."""
        nid = str(uuid.uuid4())
        note = {"note_id": nid, "title": title, "body": body, "created_at": _now_iso()}
        _notes.append(note)
        return json.dumps(note, indent=2)

    @mcp.tool()
    def external_note_search(query: str) -> str:
        """Search external notes by substring in title or body (stub)."""
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
