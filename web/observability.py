from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any


def request_id_from_headers(headers: Any) -> str:
    # Starlette/FastAPI headers are case-insensitive; keep it defensive for tests.
    rid = None
    try:
        rid = headers.get("x-request-id") or headers.get("X-Request-Id")
    except Exception:
        rid = None
    if rid:
        s = str(rid).strip()
        if s:
            return s
    return str(uuid.uuid4())


@dataclass
class Stopwatch:
    start_ns: int

    @staticmethod
    def start() -> "Stopwatch":
        return Stopwatch(start_ns=time.perf_counter_ns())

    def elapsed_ms(self) -> float:
        return (time.perf_counter_ns() - self.start_ns) / 1_000_000.0


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def json_log(logger: Any, level: str, payload: dict[str, Any]) -> None:
    """
    Emit a single structured JSON log line.
    `logger` is typically a stdlib logger; `level` is 'info'/'warning'/'error'/'debug'.
    """
    msg = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    fn = getattr(logger, level, None) or getattr(logger, "info")
    fn(msg)

