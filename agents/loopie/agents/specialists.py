"""Specialist LlmAgents: schedule (MCP calendar), tasks (Google Tasks MCP), info (DB + MCP notes)."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

from ..config import MODEL
from ..tools import db_tools
from ..tools.mcp_factory import mcp_toolset_for_agent
from ..tools.time_context import now_line_for_llm

_SCHEDULE_INSTRUCTION_STATIC = (
    "You manage the user's calendar using MCP tools only (no prose-only answers when actions/data are needed). "
    "Use REFERENCE_TIME for relative dates.\n"
    "Create: calendar_create_event(title,start_iso,end_iso?,recurrence_rules?,create_meet?,enable_transcript?). "
    "Prefer ISO-8601 with offset; date-only YYYY-MM-DD means 'pick earliest free slot' within business hours.\n"
    "Update: calendar_update_event(event_id, fields_to_change...). Invite: calendar_invite_to_event(event_id, attendee_emails).\n"
    "List: calendar_list_events(start_iso,end_iso). For meeting prep, enrich attendees via external_contact_search.\n"
    "Output MUST be compact for coordinator context: max ~10 bullets, no raw JSON. Always include event_id for relevant events."
)


def _schedule_instruction(_ctx: ReadonlyContext) -> str:
    # InstructionProvider runs each turn so 'next Wednesday' stays anchored to real time.
    return f"{now_line_for_llm()}\n\n{_SCHEDULE_INSTRUCTION_STATIC}"


_TASK_INSTRUCTION_STATIC = (
    "You manage Google Tasks and read-only calendar access via MCP. Use REFERENCE_TIME for relative dates.\n"
    "Tasks: external_task_create(title,due_iso?), external_task_list(), external_task_complete(task_id).\n"
    "Calendar read: calendar_list_events(start_iso,end_iso). Meet: meeting_transcript_read(event_id) when needed.\n"
    "Do NOT schedule events (delegate to ScheduleSpecialist).\n"
    "Output MUST be compact for coordinator context: max ~10 bullets, no raw JSON. Include task_id and due_iso when relevant."
)


def _task_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_TASK_INSTRUCTION_STATIC}"


_INFO_INSTRUCTION_STATIC = (
    "AlloyDB tools: db_notes_for_calendar_event, db_search_notes_by_keywords, db_search_notes, db_upsert_note. "
    "Use REFERENCE_TIME for relative dates. Use external_note_* only when explicitly requested.\n"
    "Performance rule: do NOT run multiple searches unless needed.\n"
    "- If you have event_id(s): call db_notes_for_calendar_event for each.\n"
    "- Else: do at most ONE db_search_notes_by_keywords (up to ~12 keywords). Only fall back to db_search_notes if zero hits.\n"
    "Output MUST be compact for coordinator context: max ~8 bullets, include note_id/title and 1-line gist; no long quotes."
)


def _info_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_INFO_INSTRUCTION_STATIC}"


def build_schedule_agent() -> LlmAgent:
    tools = [
        *mcp_toolset_for_agent(
            tool_filter=[
                "calendar_create_event",
                "calendar_list_events",
                "calendar_update_event",
                "calendar_invite_to_event",
                "external_contact_search",
            ],
            name="schedule",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="ScheduleSpecialist",
        description=(
            "Handles calendar scheduling via MCP. Use for creating, updating, inviting guests to, or listing "
            "events, meeting prep (list events plus contact search), and availability."
        ),
        instruction=_schedule_instruction,
        tools=tools,
        output_key="temp:schedule_context",
    )


def build_task_agent() -> LlmAgent:
    tools = [
        *mcp_toolset_for_agent(
            tool_filter=[
                "external_task_create",
                "external_task_list",
                "external_task_complete",
                "calendar_list_events",
                "meeting_transcript_read",
                "external_contact_search",
            ],
            name="task_mcp",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="TaskSpecialist",
        description=(
            "Manages Google Tasks via MCP, reads the calendar to plan work and spot conflicts, "
            "and uses contact search for follow-ups or disambiguation."
        ),
        instruction=_task_instruction,
        tools=tools,
        output_key="temp:task_context",
    )


def build_info_agent() -> LlmAgent:
    tools = [
        db_tools.db_notes_for_calendar_event,
        db_tools.db_search_notes_by_keywords,
        db_tools.db_search_notes,
        db_tools.db_upsert_note,
        *mcp_toolset_for_agent(
            tool_filter=["external_note_create", "external_note_search"],
            name="info_mcp",
        ),
    ]
    return LlmAgent(
        model=MODEL,
        name="InfoSpecialist",
        description=(
            "Retrieves saved AlloyDB context for the user (meeting prep, projects, people)—not only when they "
            "ask for 'notes'. Searches by event_id and keywords, returns excerpts in replies; can upsert with "
            "calendar_event_id. External notes MCP when explicitly requested."
        ),
        instruction=_info_instruction,
        tools=tools,
        output_key="temp:info_context",
    )
