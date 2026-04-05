"""Drive Loopie's coordinator + specialists with scripted prompts.

Prerequisites:
- .env loaded from repo root (Vertex/GenAI, etc.).
- MCP: MCP_USE_STDIO=1 (and PYTHONPATH=.) or a running SSE server + MCP_SSE_URL.
- Google: OAuth token for loopiedemo@gmail.com (Calendar + Tasks + Contacts scopes).
- USER_TIMEZONE set (e.g. America/Los_Angeles) for phrases like "tomorrow afternoon".
- Optional DATABASE_URL (+ migrations) so InfoSpecialist can upsert/search notes.

Free/busy demo: loopiemanager@gmail.com shares their calendar to loopiedemo with
"See only free/busy" so calendar_invite_to_event can query their busy times.

Run from repo root::

    PYTHONPATH=. python scripts/demo_loopie_workflow.py

Verbose ADK stream (optional)::

    DEMO_VERBOSE=1 PYTHONPATH=. python scripts/demo_loopie_workflow.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.loopie.agent import root_agent

APP_NAME = os.environ.get("ADK_APP_NAME", "loopie_demo")
USER_ID = os.environ.get("DEFAULT_USER_ID", "demo-user")


async def ensure_session(session_service: InMemorySessionService, session_id: str) -> None:
    existing = await session_service.get_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id,
    )
    if existing is not None:
        return None

    try:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
    except AlreadyExistsError:
        return None


async def run_prompt(
    runner: Runner,
    session_service: InMemorySessionService,
    label: str,
    text: str,
    *,
    session_id: str,
    verbose: bool,
) -> str:
    await ensure_session(session_service, session_id)
    print(
        f"\n{'=' * 60}\n{label}\nUser:\n{text}\n{'-' * 60}"
    )
    content = types.Content(
        role="user",
        parts=[types.Part(text=text)],
    )
    stream = runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=content,
    )
    final = "No response received."
    async for event in stream:
        if verbose:
            snippet = repr(event)
            if len(snippet) > 800:
                snippet = snippet[:800] + "..."
            print(f"  [event] {type(event).__name__}: {snippet}")
        if not event.is_final_response():
            continue
        if event.content and event.content.parts:
            part0 = event.content.parts[0]
            final = part0.text or final
        elif event.actions and event.actions.escalate:
            final = event.error_message or "Agent escalated."
        print(f"Loopie:\n{final}\n")
        return final

    return final


async def main() -> None:
    verbose = os.environ.get("DEMO_VERBOSE", "").lower() in ("1", "true", "yes")
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service,
    )
    scenarios = [
        (
            "1. Full handoff: schedule + tasks + notes",
            (
                "Plan Q2 review: add three prep tasks in Google Tasks, block 2 hours "
                "on my calendar next Tuesday afternoon for 'Q2 review prep', and save "
                "a short database note with tags q2,review summarizing the key decisions "
                "we want to capture."
            ),
        ),
        (
            "2. Free/busy with loopiemanager@gmail.com",
            (
                "Create a 45-minute meeting called 'Manager sync' tomorrow afternoon "
                "and invite loopiemanager@gmail.com. If they are busy then, choose "
                "another slot the same day during business hours and create the event "
                "there instead."
            ),
        ),
        (
            "3. Meeting prep (calendar + contacts + notes)",
            (
                "What should I know before my meetings in the next 48 hours? Use my "
                "calendar, contact search for attendees if needed, and any database "
                "notes that mention those people or topics."
            ),
        ),
    ]
    for label, prompt in scenarios:
        sid = f"demo-{uuid.uuid4().hex[:12]}"
        await run_prompt(
            runner,
            session_service,
            label,
            prompt,
            session_id=sid,
            verbose=verbose,
        )


if __name__ == "__main__":
    asyncio.run(main())
