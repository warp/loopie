"""Shared ADK / Vertex settings for the coordinator and specialist agents."""

from __future__ import annotations

import os

# Vertex: use a publisher model id your region supports (override via ADK_MODEL).
MODEL = os.environ.get("ADK_MODEL", "gemini-2.5-flash")
