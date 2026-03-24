"""ADK FunctionTool callables backed by AlloyDB / Postgres."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from google.adk.tools.tool_context import ToolContext

from . import db

DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "demo-user")


def _parse_json_metadata(metadata_json: str | None) -> dict[str, Any]:
    if not metadata_json or not metadata_json.strip():
        return {}
    try:
        out = json.loads(metadata_json)
        return out if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


async def db_create_task(
    title: str,
    status: str = "open",
    due_at: str | None = None,
    metadata_json: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Create a task row in the database. due_at is ISO-8601 or null."""
    del tool_context  # unused; accepted for ADK tool context injection
    pool = await db.get_pool()
    meta = _parse_json_metadata(metadata_json)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO tasks (user_id, title, status, due_at, metadata)
            VALUES ($1, $2, $3, CAST($4 AS TIMESTAMPTZ), $5::jsonb)
            RETURNING id::text, user_id, title, status,
                      to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS due_at,
                      metadata::text
            """,
            user_id,
            title,
            status,
            due_at,
            json.dumps(meta),
        )
    return dict(row) if row else {}


async def db_list_tasks(
    status: str | None = None,
    limit: int = 20,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """List recent tasks for the user, optionally filtered by status."""
    del tool_context
    pool = await db.get_pool()
    limit = max(1, min(limit, 100))
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                """
                SELECT id::text, title, status,
                       to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS due_at,
                       metadata::text, created_at
                FROM tasks
                WHERE user_id = $1 AND status = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                user_id,
                status,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id::text, title, status,
                       to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS due_at,
                       metadata::text, created_at
                FROM tasks
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
    return {"tasks": [dict(r) for r in rows]}


async def db_update_task(
    task_id: str,
    title: str | None = None,
    status: str | None = None,
    due_at: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Update a task by UUID. Only provided fields are changed."""
    del tool_context
    try:
        uuid.UUID(task_id)
    except ValueError:
        return {"error": "task_id must be a valid UUID"}
    pool = await db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tasks SET
              title = COALESCE($3, title),
              status = COALESCE($4, status),
              due_at = COALESCE(CAST($5 AS TIMESTAMPTZ), due_at),
              updated_at = now()
            WHERE id = $1::uuid AND user_id = $2
            RETURNING id::text, title, status,
              to_char(due_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS due_at
            """,
            task_id,
            user_id,
            title,
            status,
            due_at,
        )
    if not row:
        return {"error": "Task not found or not owned by user"}
    return dict(row)


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


async def db_record_calendar_cache(
    title: str,
    start_iso: str,
    end_iso: str,
    external_event_id: str | None = None,
    raw_json: str | None = None,
    user_id: str = DEFAULT_USER_ID,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Store a calendar event snapshot for cross-tool queries (optional workflow step)."""
    del tool_context
    pool = await db.get_pool()
    raw: dict[str, Any] = _parse_json_metadata(raw_json)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO calendar_event_cache
              (user_id, external_event_id, title, start_at, end_at, raw)
            VALUES ($1, $2, $3, CAST($4 AS TIMESTAMPTZ), CAST($5 AS TIMESTAMPTZ), $6::jsonb)
            RETURNING id::text, title,
              to_char(start_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS start_at,
              to_char(end_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS end_at
            """,
            user_id,
            external_event_id,
            title,
            start_iso,
            end_iso,
            json.dumps(raw),
        )
    return dict(row) if row else {}
