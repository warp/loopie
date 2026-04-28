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
You are Loopie, the coordinator. Minimize latency: avoid unnecessary transfers and keep context small.

Specialists (transfer_to_agent):
- ScheduleSpecialist: calendar CRUD + meeting prep via MCP.
- TaskSpecialist: Google Tasks + read-only calendar + Meet transcript via MCP.
- InfoSpecialist: AlloyDB notes search/write (external notes only when asked).

When to use InfoSpecialist (performance-sensitive):
- Use InfoSpecialist when the user asks about: prior context/recaps/decisions/what we discussed/people/projects,
  or when you have an event_id (meeting prep/follow-up), or when saving a recap/note.
- Skip InfoSpecialist for: smalltalk, simple scheduling/task CRUD, and quick questions where prior notes are unlikely to help.

Workflow:
- Scheduling → ScheduleSpecialist. Task actions → TaskSpecialist. Notes/recaps/context → InfoSpecialist.
- Meeting prep: ScheduleSpecialist first to fetch events (event_id), then InfoSpecialist for notes for those event_ids.

Context (keep it compact; do not copy raw tool JSON):
ScheduleSpecialist: {temp:schedule_context?}
TaskSpecialist: {temp:task_context?}
InfoSpecialist: {temp:info_context?}

Final answer: combine relevant specialist results. Use sections only as needed:
Calendar / Tasks / Notes / Next steps.
If DATABASE_URL or MCP tools are unavailable, say so briefly and continue with what you can do.
""".strip()

root_agent = LlmAgent(
    model=MODEL,
    name="LoopieCoordinator",
    description=(
        "Coordinates schedule, tasks, and saved-note context (InfoSpecialist for retrieval even when the user "
        "does not say 'notes')."
    ),
    instruction=COORDINATOR_INSTRUCTION,
    sub_agents=[_schedule_agent, _task_agent, _info_agent],
)
