"""Root ADK agent: coordinator with schedule, task, and info specialists."""

from __future__ import annotations

from google.adk.agents import LlmAgent

from .agents.specialists import build_info_agent
from .agents.specialists import build_schedule_agent
from .agents.specialists import build_task_agent
from .config import MODEL

_schedule_agent = build_schedule_agent()
_task_agent = build_task_agent()
_info_agent = build_info_agent()

COORDINATOR_INSTRUCTION = """
You are the primary coordinator for a personal assistant system.

You have three specialists (sub-agents). Use transfer_to_agent to delegate:
- ScheduleSpecialist — calendar events (MCP). Use for blocking time, listing events.
- TaskSpecialist — Google Tasks (MCP) and read-only calendar listing to plan work or check conflicts.
- InfoSpecialist — durable notes in the database and external notes MCP.

Multi-step workflows (typical order):
1) If scheduling is needed, transfer to ScheduleSpecialist first.
2) Then TaskSpecialist for tasks tied to that plan (it can list calendar events in a time window when needed).
3) Then InfoSpecialist to capture decisions or summaries.

After specialists return, synthesize a short, actionable summary for the user.
If DATABASE_URL is missing, explain that database tools will fail until it is configured.
If MCP_SSE_URL is missing and MCP is disabled, say calendar/external MCP tools are unavailable.

Demo scenario to handle well: "Plan Q2 review: add three prep tasks, block 2h on the calendar
next Tuesday afternoon, save a short note with key decisions."
""".strip()

root_agent = LlmAgent(
    model=MODEL,
    name="PersonalAssistantCoordinator",
    description=(
        "Coordinates scheduling, task management, and notes across MCP tools and AlloyDB."
    ),
    instruction=COORDINATOR_INSTRUCTION,
    sub_agents=[_schedule_agent, _task_agent, _info_agent],
)
