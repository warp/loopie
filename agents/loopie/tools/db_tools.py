"""ADK FunctionTool callables backed by AlloyDB / Postgres."""

from __future__ import annotations

import os
import uuid
from typing import Any

from google.adk.tools.tool_context import ToolContext

from . import db

DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "demo-user")

def _ilike_fragment(term: str) -> str:
    """Build a substring ILIKE pattern; strip SQL wildcards so terms stay literal."""
    t = term.strip().replace("%", "").replace("_", "")
    return f"%{t}%" if t else ""


def _norm_calendar_event_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = raw.strip()
    return s or None


async def db_upsert_note(
    title: str,
    body: str,
    tags_csv: str = "",
    note_id: str | None = None,
    calendar_event_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Create or replace a note. tags_csv is comma-separated. Pass note_id to update.

    calendar_event_id: Google Calendar event_id from calendar_list_events or calendar_create_event
    (links the note to that event for prep and follow-up). Omit when not tied to a specific event.
    """
    del tool_context
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
    cei = _norm_calendar_event_id(calendar_event_id)
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if note_id:
            try:
                uuid.UUID(note_id)
            except ValueError:
                return {"error": "note_id must be a valid UUID"}
            if calendar_event_id is not None:
                row = await conn.fetchrow(
                    """
                    UPDATE notes
                    SET title = $3, body = $4, tags = $5::text[],
                        calendar_event_id = $6, updated_at = now()
                    WHERE id = $1::uuid AND user_id = $2
                    RETURNING id::text, title, body, tags, calendar_event_id
                    """,
                    note_id,
                    user_id,
                    title,
                    body,
                    tags,
                    cei,
                )
            else:
                row = await conn.fetchrow(
                    """
                    UPDATE notes SET title = $3, body = $4, tags = $5::text[], updated_at = now()
                    WHERE id = $1::uuid AND user_id = $2
                    RETURNING id::text, title, body, tags, calendar_event_id
                    """,
                    note_id,
                    user_id,
                    title,
                    body,
                    tags,
                )
            if not row:
                return {"error": "Note not found or not owned by user"}
            return dict(row)
        row = await conn.fetchrow(
            """
            INSERT INTO notes (user_id, title, body, tags, calendar_event_id)
            VALUES ($1, $2, $3, $4::text[], $5)
            RETURNING id::text, title, body, tags, calendar_event_id
            """,
            user_id,
            title,
            body,
            tags,
            cei,
        )
    return dict(row) if row else {}


async def db_notes_for_calendar_event(
    calendar_event_id: str,
    limit: int = 20,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """List notes linked to a Google Calendar event_id (same string as calendar_list_events.event_id)."""
    del tool_context
    eid = _norm_calendar_event_id(calendar_event_id)
    if not eid:
        return {"notes": []}
    pool = await db.get_pool()
    limit = max(1, min(limit, 50))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, title, left(body, 800) AS body_preview, tags, calendar_event_id, updated_at
            FROM notes
            WHERE user_id = $1 AND calendar_event_id = $2
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            eid,
            limit,
        )
    return {"notes": [dict(r) for r in rows]}


async def db_search_notes(
    query: str,
    limit: int = 10,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search notes by title, body, or tag (single phrase, case-insensitive). Prefer db_search_notes_by_keywords for meeting prep."""
    del tool_context
    q = query.strip()
    if not q:
        return {"notes": []}
    pool = await db.get_pool()
    limit = max(1, min(limit, 50))
    pattern = _ilike_fragment(q)
    if not pattern or pattern == "%%":
        return {"notes": []}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, title, left(body, 500) AS body_preview, tags, calendar_event_id, updated_at
            FROM notes
            WHERE user_id = $1
              AND (
                title ILIKE $2
                OR body ILIKE $2
                OR EXISTS (
                  SELECT 1 FROM unnest(tags) AS tag WHERE tag ILIKE $2
                )
              )
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            pattern,
            limit,
        )
    return {"notes": [dict(r) for r in rows]}


async def db_search_notes_by_keywords(
    keywords_csv: str,
    limit: int = 20,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search notes where ANY keyword matches title, body, or a tag (meeting prep).

    Pass many comma-separated terms at once: meeting title words, attendee surnames,
    company or domain fragments, project codes, and topics from the calendar description.
    Example: "Q2,review,Acme,Sarah,standup,backend".
    """
    del tool_context
    raw = [t.strip() for t in keywords_csv.split(",") if t.strip()]
    # Drop trivial tokens; cap breadth for the planner and query planner.
    terms: list[str] = []
    seen: set[str] = set()
    for t in raw:
        key = t.casefold()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        terms.append(t)
        if len(terms) >= 24:
            break
    if not terms:
        return {"notes": [], "keywords_used": []}
    patterns: list[str] = []
    keywords_used: list[str] = []
    for t in terms:
        p = _ilike_fragment(t)
        if p:
            patterns.append(p)
            keywords_used.append(t)
    if not patterns:
        return {"notes": [], "keywords_used": []}
    pool = await db.get_pool()
    limit = max(1, min(limit, 50))
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, title, left(body, 800) AS body_preview, tags, calendar_event_id, updated_at
            FROM notes
            WHERE user_id = $1
              AND (
                title ILIKE ANY($2::text[])
                OR body ILIKE ANY($2::text[])
                OR EXISTS (
                  SELECT 1
                  FROM unnest(tags) AS tag
                  WHERE tag ILIKE ANY($2::text[])
                )
              )
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            patterns,
            limit,
        )
    return {"notes": [dict(r) for r in rows], "keywords_used": keywords_used}
