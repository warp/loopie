"""ADK FunctionTool callables backed by AlloyDB / Postgres."""

from __future__ import annotations

import os
import uuid
from typing import Any

from google.adk.tools.tool_context import ToolContext

from . import db

DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "demo-user")


async def db_upsert_note(
    title: str,
    body: str,
    tags_csv: str = "",
    note_id: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Create or replace a note. tags_csv is comma-separated. Pass note_id to update."""
    del tool_context
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()]
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        if note_id:
            try:
                uuid.UUID(note_id)
            except ValueError:
                return {"error": "note_id must be a valid UUID"}
            row = await conn.fetchrow(
                """
                UPDATE notes SET title = $3, body = $4, tags = $5::text[], updated_at = now()
                WHERE id = $1::uuid AND user_id = $2
                RETURNING id::text, title, body, tags
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
            INSERT INTO notes (user_id, title, body, tags)
            VALUES ($1, $2, $3, $4::text[])
            RETURNING id::text, title, body, tags
            """,
            user_id,
            title,
            body,
            tags,
        )
    return dict(row) if row else {}


async def db_search_notes(
    query: str,
    limit: int = 10,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Search notes by title or body (case-insensitive substring)."""
    del tool_context
    pool = await db.get_pool()
    limit = max(1, min(limit, 50))
    pattern = f"%{query}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, title, left(body, 500) AS body_preview, tags, updated_at
            FROM notes
            WHERE user_id = $1 AND (title ILIKE $2 OR body ILIKE $2)
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            user_id,
            pattern,
            limit,
        )
    return {"notes": [dict(r) for r in rows]}
