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
    "To change an existing event (title, time, location, description), call calendar_update_event with "
    "event_id from calendar_list_events and only the fields to change. "
    "To add guests to an existing event, call calendar_invite_to_event with event_id and comma-separated "
    "attendee_emails (sends invitations). "
    "To check availability or existing items, use calendar_list_events. "
    "Always use REFERENCE_TIME above when interpreting relative dates; never guess today's date. "
    "Summarize results clearly for the coordinator.\n"
    "Meeting prep: when the user asks what to know before meetings (or similar), call calendar_list_events "
    "for the relevant time window. Events may include attendees, location, description, hangout_link. "
    "For each meeting with attendees, call external_contact_search using distinct display names or email "
    "fragments to enrich with saved contact details. When you report contact matches, use each person's "
    "display_name and email (primary_email or emails); do not use or mention Google contact resource IDs. "
    "Summarize time, title, location/link, attendees, and contact highlights. If an event has no attendees, "
    "prep from title, location, and description and note that no guest list was on the event."
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
    "Return concise JSON summaries of what changed.\n"
    "Follow-ups: when the user wants next steps after a meeting or to capture action items, use "
    "calendar_list_events if you need to anchor which meeting (time/title). Create tasks with clear titles "
    "(include meeting title or person name when helpful) and due_iso in RFC3339; default due dates to the "
    "next business day unless the user specifies otherwise. Use external_contact_search when a person "
    "reference is ambiguous; cite display_name and email from results, not resource IDs."
)


def _task_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_TASK_INSTRUCTION_STATIC}"


_INFO_INSTRUCTION_STATIC = (
    "Use db_upsert_note and db_search_notes for canonical notes in AlloyDB. "
    "tags_csv is comma-separated. "
    "Use external_note_* MCP tools when the user explicitly asks for the external notes integration. "
    "Prefer the database for durable project notes. "
    "Use REFERENCE_TIME when the user refers to relative dates (e.g. notes from last week).\n"
    "Meeting prep: when helping prepare for meetings, use db_search_notes for attendee names, companies, "
    "or project keywords to surface relevant context."
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
    )


def build_task_agent() -> LlmAgent:
    tools = [
        *mcp_toolset_for_agent(
            tool_filter=[
                "external_task_create",
                "external_task_list",
                "external_task_complete",
                "calendar_list_events",
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
