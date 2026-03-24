"""Build MCP toolsets for ADK (SSE for Cloud Run / remote, stdio for local)."""

from __future__ import annotations

import logging
import os
import sys

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import SseConnectionParams
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

logger = logging.getLogger(__name__)


def _mcp_disabled_by_env() -> bool:
    return os.environ.get("MCP_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def mcp_toolset_for_agent(
    *,
    tool_filter: list[str],
    name: str = "mcp",
) -> list[McpToolset]:
    """Return a list with one McpToolset, or empty if MCP is disabled."""
    if _mcp_disabled_by_env():
        logger.info("MCP skipped for %s (MCP_DISABLED is set)", name)
        return []

    sse_url = os.environ.get("MCP_SSE_URL", "").strip()
    use_stdio = os.environ.get("MCP_USE_STDIO", "").lower() in ("1", "true", "yes")

    if use_stdio:
        cmd = os.environ.get("MCP_STDIO_COMMAND", sys.executable)
        args_env = os.environ.get("MCP_STDIO_ARGS", "")
        if args_env:
            args_list = args_env.split()
        else:
            # Default: run bundled stub server (stdio) — repo root must be on PYTHONPATH.
            args_list = ["-m", "mcp_servers.app", "stdio"]
        logger.info("MCP stdio: command=%s args=%s", cmd, args_list)
        return [
            McpToolset(
                connection_params=StdioConnectionParams(
                    server_params=StdioServerParameters(
                        command=cmd,
                        args=args_list,
                        env={**os.environ},
                    ),
                ),
                tool_filter=tool_filter,
            )
        ]

    if not sse_url:
        logger.info(
            "MCP disabled for %s (set MCP_SSE_URL or MCP_USE_STDIO=1)",
            name,
        )
        return []

    return [
        McpToolset(
            connection_params=SseConnectionParams(url=sse_url),
            tool_filter=tool_filter,
        )
    ]
