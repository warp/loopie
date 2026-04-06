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
    "For new events, call calendar_create_event: use ISO-8601 start_iso with a timezone offset when possible; "
    "naive times are interpreted as local wall time in the user's calendar zone (REFERENCE_TIME / USER_TIMEZONE). "
    "Optional end_iso; "
    "if end_iso is omitted, duration follows the user's default event length from Calendar settings. "
    "If only a day is known (no time), pass start_iso as date-only YYYY-MM-DD to place the event in the "
    "earliest free slot that day (or later) within BUSINESS_HOURS_* / BUSINESS_DAYS env (mirror Calendar working hours). "
    "For recurring events, pass recurrence_rules: one RFC 5545 line per newline—full RRULE:/EXDATE:/RDATE: lines, "
    "or a line without the RRULE: prefix (e.g. FREQ=WEEKLY;BYDAY=TU,TH or FREQ=DAILY;COUNT=10). "
    "To change an existing event (title, time, location, description, recurrence), call calendar_update_event with "
    "event_id from calendar_list_events and only the fields to change; use recurrence_clear=True to make an event non-recurring. "
    "To add guests to an existing event, call calendar_invite_to_event with event_id and comma-separated "
    "attendee_emails (sends invitations). The server checks invitees' free/busy when their calendar is "
    "shared with you; conflicts return attendee_busy unless disabled via env. "
    "To check availability or existing items, use calendar_list_events. "
    "Always use REFERENCE_TIME above when interpreting relative dates; never guess today's date. "
    "Summarize results clearly for the coordinator.\n"
    "Meeting prep: when the user asks what to know before meetings (or similar), call calendar_list_events "
    "for the relevant time window. Events may include attendees, location, description, hangout_link. "
    "For each meeting with attendees, call external_contact_search using distinct display names or email "
    "fragments to enrich with saved contact details. When you report contact matches, use each person's "
    "display_name and email (primary_email or emails); do not use or mention Google contact resource IDs. "
    "Summarize time, title, location/link, attendees, and contact highlights. If an event has no attendees, "
    "prep from title, location, and description and note that no guest list was on the event.\n"
    "Every calendar_list_events item includes event_id. Include each relevant event_id in your handoff to "
    "the coordinator so notes can be linked and retrieved for that meeting.\n"
    "At the end of every substantive reply, add a short block exactly like:\n"
    "Coordinator handoff (schedule):\n"
    "- …bullets with concrete outcomes (event title, start/end or slot, event_id, invitee errors, etc.).\n"
    "The coordinator merges several specialists; this block must be scannable so nothing from this step is lost."
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
    "reference is ambiguous; cite display_name and email from results, not resource IDs.\n"
    "At the end of every substantive reply, add:\n"
    "Coordinator handoff (tasks):\n"
    "- …bullets (task titles created, task_ids, dues, list highlights, or explicit \"no task changes\")."
)


def _task_instruction(_ctx: ReadonlyContext) -> str:
    return f"{now_line_for_llm()}\n\n{_TASK_INSTRUCTION_STATIC}"


_INFO_INSTRUCTION_STATIC = (
    "AlloyDB is canonical: db_upsert_note, db_search_notes, db_search_notes_by_keywords, db_notes_for_calendar_event. "
    "tags_csv is comma-separated. Set calendar_event_id on db_upsert_note when the note is tied to a Google "
    "Calendar event_id from calendar JSON. Use external_note_* only when the user explicitly asks for that integration. "
    "Use REFERENCE_TIME for relative dates.\n"
    "The user often does not say the word 'notes'—still run searches and **surface results in your reply** "
    "(summaries or short quotes from body_preview). Lead with a line like 'From your saved notes:' when you "
    "have hits; if nothing matched, say 'No matching saved notes found' so the coordinator can merge honestly.\n"
    "Always check before you write or before a substantive answer. Order: (1) event_id in handoff or message → "
    "db_notes_for_calendar_event for each distinct id. (2) db_search_notes_by_keywords with a broad CSV from "
    "titles, names, companies, project codes, tags, and request keywords. (3) If thin, db_search_notes on "
    "2–4 strong phrases. (4) Then db_upsert_note or finalize, merging findings into the answer—do not hide "
    "useful notes in tool output only; repeat the gist for the user.\n"
    "At the end of every substantive reply, add:\n"
    "Coordinator handoff (notes):\n"
    "- …bullets (note titles/ids written, tags, calendar_event_id linked, search hits count, or \"no DB writes\").\n"
    "Exception: skip searches only for pure external_note_* with no AlloyDB angle, or a trivial ack."
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
    )
