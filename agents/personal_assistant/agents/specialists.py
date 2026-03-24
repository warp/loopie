"""Specialist LlmAgents: schedule (MCP calendar), tasks (DB + MCP), info (DB + MCP notes)."""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..tools import db_tools
from ..tools.mcp_factory import mcp_toolset_for_agent
from ..tools.time_context import now_line_for_llm

MODEL = os.environ.get("ADK_MODEL", "gemini-2.5-flash")

_SCHEDULE_INSTRUCTION_STATIC = (
    "You manage the user's calendar using MCP tools only. "
    "For new events, call calendar_create_event with ISO-8601 start_iso and end_iso. "
    "To check availability or existing items, use calendar_list_events. "
    "Always use REFERENCE_TIME above when interpreting relative dates; never guess today's date. "
    "Summarize results clearly for the coordinator."
)


def _schedule_instruction(_ctx: ReadonlyContext) -> str:
    # InstructionProvider runs each turn so 'next Wednesday' stays anchored to real time.
    return f"{now_line_for_llm()}\n\n{_SCHEDULE_INSTRUCTION_STATIC}"


def build_schedule_agent() -> LlmAgent:
    tools = [
        *mcp_toolset_for_agent(
            tool_filter=["calendar_create_event", "calendar_list_events"],
            name="schedule",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="ScheduleSpecialist",
        description="Handles calendar scheduling via MCP calendar tools. Use for creating or listing calendar events.",
        instruction=_schedule_instruction,
        tools=tools,
    )


def build_task_agent() -> LlmAgent:
    tools = [
        db_tools.db_create_task,
        db_tools.db_list_tasks,
        db_tools.db_update_task,
        db_tools.db_record_calendar_cache,
        *mcp_toolset_for_agent(
            tool_filter=[
                "external_task_create",
                "external_task_list",
                "external_task_complete",
            ],
            name="task_mcp",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="TaskSpecialist",
        description=(
            "Manages structured tasks in the database and optional external task MCP. "
            "Use for CRUD on tasks, listing work items, or recording calendar snapshots in DB."
        ),
        instruction=(
            "You own task data in the database: create, list, and update tasks with the db_* tools. "
            "Use ISO-8601 for due_at. "
            "When the user also uses an external task system, use external_task_* MCP tools. "
            "If asked to persist a calendar event for later SQL queries, use db_record_calendar_cache. "
            "Return concise JSON summaries of what changed."
        ),
        tools=tools,
    )


def build_info_agent() -> LlmAgent:
    tools = [
        db_tools.db_upsert_note,
        db_tools.db_search_notes,
        *mcp_toolset_for_agent(
            tool_filter=["external_note_create", "external_note_search"],
            name="info_mcp",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="InfoSpecialist",
        description="Stores and searches notes in the database and external notes MCP.",
        instruction=(
            "Use db_upsert_note and db_search_notes for canonical notes in AlloyDB. "
            "tags_csv is comma-separated. "
            "Use external_note_* MCP tools when the user explicitly asks for the external notes integration. "
            "Prefer the database for durable project notes."
        ),
        tools=tools,
    )
