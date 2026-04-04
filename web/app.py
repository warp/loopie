from __future__ import annotations

import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

# Load .env before importing the agent: specialist MCP toolsets read MCP_SSE_URL / MCP_* at import time.
REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.loopie.agent import root_agent
from mcp_servers import people_google

logger = logging.getLogger(__name__)

APP_NAME = os.environ.get("ADK_APP_NAME", "loopie_web")
DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "demo-user")

session_service = InMemorySessionService()
runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)


def _mcp_sse_tcp_reachable(url: str, timeout: float = 0.5) -> bool:
    try:
        u = urlparse(url)
        host = u.hostname
        if not host:
            return False
        port = u.port
        if port is None:
            port = 443 if (u.scheme or "http") == "https" else 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    sse = os.environ.get("MCP_SSE_URL", "").strip()
    use_stdio = os.environ.get("MCP_USE_STDIO", "").lower() in ("1", "true", "yes")
    disabled = os.environ.get("MCP_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if sse and not disabled and not use_stdio:
        ok = await asyncio.to_thread(_mcp_sse_tcp_reachable, sse)
        if not ok:
            logger.warning(
                "MCP_SSE_URL is set (%s) but nothing is accepting TCP connections there. "
                "Calendar/tasks MCP will fail until you start the server "
                "(PYTHONPATH=. python -m mcp_servers.app sse) or set MCP_USE_STDIO=1.",
                sse,
            )
    yield


app = FastAPI(title="Loopie Web", version="0.1.0", lifespan=_lifespan)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/contacts")
async def api_contacts(q: str, limit: int = 10) -> Any:
    # Keep this endpoint thin: just return the normalized JSON produced by the People helper.
    # We return parsed JSON so the frontend doesn't have to parse strings.
    import json

    raw = people_google.search_contacts(q, limit)
    return json.loads(raw)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = Field(default="default")
    user_id: str = Field(default=DEFAULT_USER_ID)


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    response: str


@app.post("/api/chat", response_model=ChatResponse)
async def api_chat(req: ChatRequest) -> ChatResponse:
    existing = await session_service.get_session(
        app_name=APP_NAME, user_id=req.user_id, session_id=req.session_id
    )
    if existing is None:
        try:
            await session_service.create_session(
                app_name=APP_NAME, user_id=req.user_id, session_id=req.session_id
            )
        except AlreadyExistsError:
            pass

    content = types.Content(role="user", parts=[types.Part(text=req.message)])
    events = runner.run_async(
        user_id=req.user_id, session_id=req.session_id, new_message=content
    )

    final_text = "No response received."
    try:
        async for event in events:
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_text = event.content.parts[0].text or final_text
                elif event.actions and event.actions.escalate:
                    final_text = event.error_message or "Agent escalated."
                break
    except ConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "mcp_unreachable",
                "message": str(e),
                "hint": (
                    "Nothing is listening at MCP_SSE_URL (connection refused). "
                    "Start MCP in another terminal: PYTHONPATH=. python -m mcp_servers.app sse — "
                    "or use in-process MCP: MCP_USE_STDIO=1 (and PYTHONPATH=. so mcp_servers imports work)."
                ),
            },
        ) from e

    return ChatResponse(session_id=req.session_id, user_id=req.user_id, response=final_text)

