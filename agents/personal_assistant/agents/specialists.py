"""Specialist LlmAgents: schedule (MCP calendar), tasks (Google Tasks MCP), info (DB + MCP notes)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..config import MODEL
from ..tools import db_tools
from ..tools.mcp_factory import mcp_toolset_for_agent
from ..tools.time_context import now_line_for_llm

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


_TASK_INSTRUCTION_STATIC = (
    "You manage Google Tasks and read-only calendar access via MCP. "
    "For tasks: use external_task_create (optional RFC3339 due_iso), external_task_list, "
    "external_task_complete (task_id from create/list). "
    "To see what's on the calendar when planning tasks or checking conflicts, call calendar_list_events "
    "with ISO-8601 start_iso and end_iso for the window you need. "
    "You do not create or edit calendar events; delegate scheduling to ScheduleSpecialist. "
    "Always use REFERENCE_TIME when interpreting relative dates. "
    "Return concise JSON summaries of what changed."
)


def _task_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_TASK_INSTRUCTION_STATIC}"


_INFO_INSTRUCTION_STATIC = (
    "Use db_upsert_note and db_search_notes for canonical notes in AlloyDB. "
    "tags_csv is comma-separated. "
    "Use external_note_* MCP tools when the user explicitly asks for the external notes integration. "
    "Prefer the database for durable project notes. "
    "Use REFERENCE_TIME when the user refers to relative dates (e.g. notes from last week)."
)


def _info_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_INFO_INSTRUCTION_STATIC}"


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
        *mcp_toolset_for_agent(
            tool_filter=[
                "external_task_create",
                "external_task_list",
                "external_task_complete",
                "calendar_list_events",
            ],
            name="task_mcp",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="TaskSpecialist",
        description=(
            "Manages Google Tasks via MCP and reads the calendar (list events) to plan work and spot conflicts."
        ),
        instruction=_task_instruction,
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
        instruction=_info_instruction,
        tools=tools,
    )
