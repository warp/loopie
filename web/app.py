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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.loopie.agent import root_agent
from agents.loopie.tools import db as loopie_db
from mcp_servers import people_google
from web.observability import Stopwatch
from web.observability import env_int
from web.observability import json_log
from web.observability import request_id_from_headers

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
        sw = Stopwatch.start()
        ok = await asyncio.to_thread(_mcp_sse_tcp_reachable, sse)
        json_log(
            logger,
            "info" if ok else "warning",
            {
                "event": "mcp_sse_reachability",
                "mcp_sse_url": sse,
                "ok": ok,
                "elapsed_ms": round(sw.elapsed_ms(), 3),
            },
        )
        if not ok:
            logger.warning(
                "MCP_SSE_URL is set (%s) but nothing is accepting TCP connections there. "
                "Calendar/tasks MCP will fail until you start the server "
                "(PYTHONPATH=. python -m mcp_servers.app sse) or set MCP_USE_STDIO=1.",
                sse,
            )

    if os.environ.get("WARM_DB_ON_STARTUP", "").strip().lower() in ("1", "true", "yes", "on"):
        sw = Stopwatch.start()
        try:
            if loopie_db.database_url():
                await loopie_db.get_pool()
                json_log(
                    logger,
                    "info",
                    {"event": "db_pool_warmup", "ok": True, "elapsed_ms": round(sw.elapsed_ms(), 3)},
                )
            else:
                json_log(logger, "info", {"event": "db_pool_warmup", "ok": False, "reason": "no_database_url"})
        except Exception as e:
            json_log(
                logger,
                "warning",
                {
                    "event": "db_pool_warmup",
                    "ok": False,
                    "elapsed_ms": round(sw.elapsed_ms(), 3),
                    "error": str(e),
                },
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
async def api_chat(req: ChatRequest, request: Request) -> ChatResponse:
    request_id = request_id_from_headers(request.headers)
    total_sw = Stopwatch.start()
    slow_ms = env_int("SLOW_CHAT_MS", 2500)
    outcome = "ok"
    agent_error: str | None = None
    response_len = 0
    message_len = len(req.message or "")

    t_session_ms = 0.0
    t_agent_ms = 0.0
    t_finalize_ms = 0.0

    sw = Stopwatch.start()
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
    t_session_ms = sw.elapsed_ms()

    content = types.Content(role="user", parts=[types.Part(text=req.message)])
    sw = Stopwatch.start()
    events = runner.run_async(user_id=req.user_id, session_id=req.session_id, new_message=content)

    final_text = "No response received."
    try:
        async for event in events:
            if event.is_final_response():
                if event.content and event.content.parts:
                    final_text = event.content.parts[0].text or final_text
                elif event.actions and event.actions.escalate:
                    final_text = event.error_message or "Agent escalated."
                    outcome = "escalate"
                break
    except ConnectionError as e:
        outcome = "error"
        agent_error = str(e).strip() or "ConnectionError"
        sse = os.environ.get("MCP_SSE_URL", "").strip()
        raw = str(e).strip()
        # ADK often raises ConnectionError with an empty suffix after "Failed to create MCP session:".
        if not raw or raw.rstrip(":").strip() == "Failed to create MCP session":
            msg = (
                f"Could not open an MCP session to {sse!r} (nothing listening or connection failed)."
                if sse
                else "Could not open an MCP session (SSE unreachable)."
            )
        else:
            msg = raw
        detail: dict[str, Any] = {
            "error": "mcp_unreachable",
            "message": msg,
            "hint": (
                "Nothing is listening at MCP_SSE_URL (connection refused). "
                "From the repo root, start MCP: PYTHONPATH=. python -m mcp_servers.app sse "
                "(default port 8765; keep .env MCP_SSE_URL in sync, e.g. http://127.0.0.1:8765/sse). "
                "Or use in-process MCP: MCP_USE_STDIO=1 (and PYTHONPATH=. so mcp_servers imports work). "
                "To run without calendar/MCP tools until the server is up: unset MCP_SSE_URL or set MCP_DISABLED=1."
            ),
        }
        if sse:
            detail["mcp_sse_url"] = sse
        t_agent_ms = sw.elapsed_ms()
        json_log(
            logger,
            "warning" if total_sw.elapsed_ms() >= slow_ms else "info",
            {
                "event": "chat_request",
                "request_id": request_id,
                "user_id": req.user_id,
                "session_id": req.session_id,
                "message_len": message_len,
                "outcome": outcome,
                "error": agent_error,
                "timings_ms": {
                    "session": round(t_session_ms, 3),
                    "agent": round(t_agent_ms, 3),
                    "finalize": round(t_finalize_ms, 3),
                    "total": round(total_sw.elapsed_ms(), 3),
                },
            },
        )
        raise HTTPException(status_code=503, detail=detail) from e

    t_agent_ms = sw.elapsed_ms()
    sw = Stopwatch.start()
    response_len = len(final_text or "")
    t_finalize_ms = sw.elapsed_ms()

    total_ms = total_sw.elapsed_ms()
    json_log(
        logger,
        "warning" if total_ms >= slow_ms else "info",
        {
            "event": "chat_request",
            "request_id": request_id,
            "user_id": req.user_id,
            "session_id": req.session_id,
            "message_len": message_len,
            "response_len": response_len,
            "outcome": outcome,
            "timings_ms": {
                "session": round(t_session_ms, 3),
                "agent": round(t_agent_ms, 3),
                "finalize": round(t_finalize_ms, 3),
                "total": round(total_ms, 3),
            },
        },
    )

    return ChatResponse(session_id=req.session_id, user_id=req.user_id, response=final_text)

